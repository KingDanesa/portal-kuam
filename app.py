from flask import Flask, render_template, request, redirect, session
import sqlite3
from datetime import datetime, date
from flask import jsonify
from openai import OpenAI
import dateparser
import re
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'secret'
DATABASE = 'attendance_portal.db'

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username=? AND password=?", (username, password)).fetchone()
        if user:
            session['user_id'] = user['id']
            session['role'] = user['role']
            if user['role'] == 'teacher':
                return redirect('/teacher/dashboard')
            elif user['role'] == 'student':
                return redirect('/student/dashboard')
            elif user['role'] == 'auditor':
                return redirect('/auditor/dashboard')
            else:
                return "Неизвестная роль"
        else:
            return "Неверный логин или пароль"
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

@app.route('/auditor/dashboard')
def auditor_dashboard():
    if session.get('role') != 'auditor':
        return redirect('/login')

    db = get_db()
    selected_date = request.args.get('selected_date')
    selected_teacher_id = request.args.get('teacher_id')
    selected_subject_id = request.args.get('subject_id')
    selected_group_id = request.args.get('group_id')

    teachers = []
    subjects = []
    groups = []
    schedule = []

    if selected_date:
        teachers = db.execute("""
            SELECT DISTINCT users.id, users.username
            FROM users
            JOIN subjects ON users.id = subjects.teacher_id
            JOIN schedules ON subjects.id = schedules.subject_id
            WHERE users.role = 'teacher' AND schedules.date = ?
        """, (selected_date,)).fetchall()

        if selected_teacher_id:
            subjects = db.execute("""
                SELECT id, name FROM subjects WHERE teacher_id = ?
            """, (selected_teacher_id,)).fetchall()

        if selected_teacher_id and selected_subject_id and selected_date:
            groups = db.execute("""
                SELECT DISTINCT groups.id, groups.name
                FROM groups
                JOIN schedules ON groups.id = schedules.group_id
                JOIN subjects ON schedules.subject_id = subjects.id
                WHERE schedules.subject_id = ?
                  AND subjects.teacher_id = ?
                  AND schedules.date = ?
            """, (selected_subject_id, selected_teacher_id, selected_date)).fetchall()

        if selected_teacher_id and selected_subject_id and selected_group_id:
            schedule = db.execute("""
                SELECT schedules.id, schedules.time,
                       groups.name as group_name, subjects.name as subject
                FROM schedules
                JOIN subjects ON schedules.subject_id = subjects.id
                JOIN groups ON schedules.group_id = groups.id
                WHERE schedules.date = ? AND subjects.teacher_id = ?
                  AND subjects.id = ? AND groups.id = ?
            """, (selected_date, selected_teacher_id, selected_subject_id, selected_group_id)).fetchall()

    return render_template('auditor_dashboard.html',
                           teachers=teachers,
                           subjects=subjects,
                           groups=groups,
                           schedule=schedule,
                           selected_date=selected_date,
                           selected_teacher_id=selected_teacher_id,
                           selected_subject_id=selected_subject_id,
                           selected_group_id=selected_group_id)

@app.route('/auditor/compare/<int:schedule_id>')
def compare_attendance(schedule_id):
    if session.get('role') != 'auditor':
        return redirect('/login')

    db = get_db()
    comparison = db.execute("""
        SELECT s.full_name,
               a.status AS auditor_status,
               t.status AS teacher_status
        FROM students s
        LEFT JOIN auditor_attendance a ON s.id = a.student_id AND a.schedule_id = ?
        LEFT JOIN schedules sch ON sch.id = ?
        LEFT JOIN attendance t ON s.id = t.student_id AND t.subject_id = sch.subject_id AND t.date = sch.date
        WHERE s.group_id = (SELECT group_id FROM schedules WHERE id = ?)
    """, (schedule_id, schedule_id, schedule_id)).fetchall()

    def translate_teacher(status):
        return 'Присутствовал' if status == 'present' else ('Отсутствовал' if status == 'absent' else status)

    def translate_auditor(status):
        return status.capitalize() if status else "-"

    rows = []
    matched = 0

    for row in comparison:
        teacher_translated = translate_teacher(row['teacher_status'])
        auditor_translated = translate_auditor(row['auditor_status'])
        is_match = teacher_translated == auditor_translated
        if is_match:
            matched += 1
        rows.append({
            'full_name': row['full_name'],
            'teacher_status': teacher_translated,
            'auditor_status': auditor_translated,
            'match': is_match
        })

    match_percent = round((matched / len(rows)) * 100, 2) if rows else None

    return render_template("compare.html", comparison=rows, match_percent=match_percent)

@app.route('/auditor/report', methods=['GET'])
def auditor_report():
    if session.get('role') != 'auditor':
        return redirect('/login')

    db = get_db()
    teachers = db.execute("SELECT id, username FROM users WHERE role = 'teacher'").fetchall()
    subjects = db.execute("SELECT id, name FROM subjects").fetchall()

    selected_teacher_id = request.args.get('teacher_id')
    selected_subject_id = request.args.get('subject_id')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    report_data = []

    if selected_teacher_id and selected_subject_id and start_date and end_date:
        report_data = db.execute("""
            SELECT st.full_name,
                   SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) AS present_count,
                   SUM(CASE WHEN a.status = 'absent' THEN 1 ELSE 0 END) AS absent_count,
                   COUNT(a.status) AS total
            FROM attendance a
            JOIN students st ON a.student_id = st.id
            WHERE a.subject_id = ?
              AND a.date BETWEEN ? AND ?
              AND EXISTS (
                SELECT 1 FROM subjects s
                WHERE s.id = a.subject_id AND s.teacher_id = ?
              )
            GROUP BY st.id
        """, (selected_subject_id, start_date, end_date, selected_teacher_id)).fetchall()

    return render_template('report.html',
                           teachers=teachers,
                           subjects=subjects,
                           selected_teacher_id=selected_teacher_id,
                           selected_subject_id=selected_subject_id,
                           start_date=start_date,
                           end_date=end_date,
                           report_data=report_data)

@app.route('/student/dashboard')
def student_dashboard():
    if session.get('role') != 'student':
        return redirect('/login')
    return "<h2>Вы вошли как студент</h2>"

@app.route('/teacher/dashboard', methods=['GET'])
def teacher_dashboard():
    if session.get('role') != 'teacher':
        return redirect('/login')
    db = get_db()
    teacher_id = session['user_id']
    selected_date = request.args.get('selected_date')
    if not selected_date:
        selected_date = date.today().isoformat()

    filtered_schedule = db.execute("""
        SELECT schedules.id, schedules.date, schedules.time,
               subjects.name AS subject, groups.name AS group_name
        FROM schedules
        JOIN subjects ON schedules.subject_id = subjects.id
        JOIN groups ON schedules.group_id = groups.id
        WHERE subjects.teacher_id = ? AND schedules.date = ?
    """, (teacher_id, selected_date)).fetchall()
    # Если не найдено обычное расписание — ищем цикличное
    if not filtered_schedule:
        weekday_name = datetime.strptime(selected_date, '%Y-%m-%d').strftime('%A')
        weekday_map = {
            'Monday': 'Понедельник',
            'Tuesday': 'Вторник',
            'Wednesday': 'Среда',
            'Thursday': 'Четверг',
            'Friday': 'Пятница',
            'Saturday': 'Суббота',
            'Sunday': 'Воскресенье'
        }
        day_of_week = weekday_map.get(weekday_name)

        # Получим username преподавателя
        teacher_username = db.execute("SELECT username FROM users WHERE id = ?", (teacher_id,)).fetchone()['username']

        filtered_schedule = db.execute("""
            SELECT st.day_of_week, st.time, st.subject, g.name AS group_name
            FROM schedule_template st
            JOIN groups g ON st.group_id = g.id
            WHERE st.teacher = ? AND st.day_of_week = ?
            ORDER BY st.time
        """, (teacher_username, day_of_week)).fetchall()


    return render_template('teacher.html', filtered_schedule=filtered_schedule,
                           selected_date=selected_date)

@app.route('/take-attendance/<int:schedule_id>', methods=['GET', 'POST'])
def take_attendance(schedule_id):
    if session.get('role') != 'teacher':
        return redirect('/login')
    db = get_db()
    if request.method == 'POST':
        subject_id = db.execute("SELECT subject_id FROM schedules WHERE id=?", (schedule_id,)).fetchone()['subject_id']
        students = db.execute("""
            SELECT students.id FROM students
            JOIN groups ON students.group_id = groups.id
            JOIN schedules ON schedules.group_id = groups.id
            WHERE schedules.id = ?
        """, (schedule_id,)).fetchall()

        for student in students:
            status = request.form.get(f'status_{student["id"]}')
            db.execute("INSERT INTO attendance (student_id, subject_id, date, status) VALUES (?, ?, ?, ?)",
                       (student['id'], subject_id, datetime.now().strftime("%Y-%m-%d"), status))
        db.commit()
        return redirect('/teacher/dashboard')

    students = db.execute("""
        SELECT students.id, students.full_name FROM students
        JOIN groups ON students.group_id = groups.id
        JOIN schedules ON schedules.group_id = groups.id
        WHERE schedules.id = ?
    """, (schedule_id,)).fetchall()

    return render_template('take_attendance.html', students=students)

@app.route('/auditor/take-attendance/<int:schedule_id>', methods=['GET', 'POST'])
def auditor_take_attendance(schedule_id):
    if session.get('role') != 'auditor':
        return redirect('/login')
    db = get_db()
    if request.method == 'POST':
        students = db.execute("""
            SELECT students.id FROM students
            JOIN groups ON students.group_id = groups.id
            JOIN schedules ON schedules.group_id = groups.id
            WHERE schedules.id = ?
        """, (schedule_id,)).fetchall()
        for student in students:
            status = request.form.get(f'status_{student["id"]}')
            db.execute("INSERT INTO auditor_attendance (student_id, schedule_id, status) VALUES (?, ?, ?)",
                       (student['id'], schedule_id, status))
        db.commit()
        return redirect('/auditor/dashboard')

    students = db.execute("""
        SELECT students.id, students.full_name FROM students
        JOIN groups ON students.group_id = groups.id
        JOIN schedules ON schedules.group_id = groups.id
        WHERE schedules.id = ?
    """, (schedule_id,)).fetchall()

    return render_template('auditor_take_attendance.html', students=students)

@app.route('/auditor/view-teacher-attendance/<int:schedule_id>')
def view_teacher_attendance(schedule_id):
    if session.get('role') != 'auditor':
        return redirect('/login')
    db = get_db()
    rows = db.execute("""
        SELECT students.full_name, attendance.status
        FROM attendance
        JOIN students ON attendance.student_id = students.id
        WHERE attendance.subject_id = (
            SELECT subject_id FROM schedules WHERE id = ?
        ) AND attendance.date = (
            SELECT date FROM schedules WHERE id = ?
        ) AND students.group_id = (
            SELECT group_id FROM schedules WHERE id = ?
        )
    """, (schedule_id, schedule_id, schedule_id)).fetchall()

    return render_template('view_teacher_attendance.html', records=rows)


from flask import make_response, request, jsonify
from xhtml2pdf import pisa
import io

@app.route('/auditor/report/pdf', methods=['POST'])
def report_pdf():
    if session.get('role') != 'auditor':
        return redirect('/login')

    db = get_db()
    data = request.get_json()

    start_date = data.get('start_date')
    end_date = data.get('end_date')
    teacher_id = data.get('teacher_id')
    subject_id = data.get('subject_id')

    teacher_name = db.execute("SELECT username FROM users WHERE id = ?", (teacher_id,)).fetchone()['username']
    subject_name = db.execute("SELECT name FROM subjects WHERE id = ?", (subject_id,)).fetchone()['name']
    group_name = db.execute("""
        SELECT DISTINCT g.name FROM schedules sc
        JOIN groups g ON sc.group_id = g.id
        WHERE sc.subject_id = ? AND sc.teacher_id = ?
    """, (subject_id, teacher_id)).fetchone()['name']

    rows = db.execute("""
        SELECT st.full_name,
               SUM(CASE WHEN a.status = 'present' THEN 1 ELSE 0 END) AS present_count,
               SUM(CASE WHEN a.status = 'absent' THEN 1 ELSE 0 END) AS absent_count,
               COUNT(a.status) AS total
        FROM attendance a
        JOIN students st ON a.student_id = st.id
        WHERE a.subject_id = ?
          AND a.date BETWEEN ? AND ?
          AND EXISTS (
            SELECT 1 FROM subjects s
            WHERE s.id = a.subject_id AND s.teacher_id = ?
          )
        GROUP BY st.id
    """, (subject_id, start_date, end_date, teacher_id)).fetchall()

    total_present = sum(row['present_count'] for row in rows)
    total_total = sum(row['total'] for row in rows)
    group_average_percent = round((total_present / total_total) * 100, 2) if total_total > 0 else 0

    html = render_template("report_pdf_template.html",
                           data=rows,
                           start_date=start_date,
                           end_date=end_date,
                           teacher_name=teacher_name,
                           subject_name=subject_name,
                           group_name=group_name,
                           group_average_percent=group_average_percent)

    result = io.BytesIO()
    pisa_status = pisa.CreatePDF(io.StringIO(html), dest=result)

    if pisa_status.err:
        return jsonify({"error": "Ошибка при создании PDF"}), 500

    response = make_response(result.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = "attachment; filename=report.pdf"
    return response




@app.route('/create-schedule', methods=['POST'])
def create_schedule():
    if session.get('role') != 'teacher':
        return redirect('/login')

    selected_date = request.form['date']
    time = request.form['time']
    subject_name = request.form['subject']
    group_name = request.form['group']

    db = get_db()
    teacher_id = session['user_id']

    # Найдём group_id и subject_id
    group = db.execute("SELECT id FROM groups WHERE name = ?", (group_name,)).fetchone()
    subject = db.execute("SELECT id FROM subjects WHERE name = ?", (subject_name,)).fetchone()

    if not group or not subject:
        return "Группа или предмет не найдены", 400

    # Проверим, не существует ли уже такая запись
    existing = db.execute("""
        SELECT id FROM schedules
        WHERE date = ? AND time = ? AND group_id = ? AND subject_id = ? AND teacher_id = ?
    """, (selected_date, time, group['id'], subject['id'], teacher_id)).fetchone()

    if existing:
        return redirect(f"/take-attendance/{existing['id']}")

    # Вставка
    db.execute("""
        INSERT INTO schedules (date, time, group_id, subject_id, teacher_id)
        VALUES (?, ?, ?, ?, ?)
    """, (selected_date, time, group['id'], subject['id'], teacher_id))
    db.commit()

    new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return redirect(f"/take-attendance/{new_id}")


# ИИ-чат для аудитора

client = OpenAI(api_key="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")  # 🔁 Заменишь на свой ключ

def generate_sql_from_prompt(prompt):
    system_msg = """Ты ассистент, который помогает анализировать посещаемость студентов. 
Ты получаешь текстовый запрос и должен вернуть ТОЛЬКО SQL-запрос (без комментариев и пояснений), чтобы извлечь нужные данные из базы.

Таблицы базы:
- users(id, username, password, role)
- students(id, full_name, group_id)
- groups(id, name)
- subjects(id, name, teacher_id)
- schedules(id, date, time, group_id, subject_id, teacher_id)
- attendance(id, student_id, subject_id, date, status)

Примеры:
1. "Посещаемость по студенту Иванов Иван Иванович" → нужно найти его ID по `students.full_name`, затем показать все даты, предметы и статус из `attendance`, где `student_id` = его ID.
2. "Посещаемость по преподавателю Ахметов" → найти `users.id`, затем все `subjects`, затем все `schedules` и соответствующую посещаемость.
3. "Посещаемость по предмету Математика" → найти `subjects.id` и показать всех студентов с их статусами по этому предмету.
4. Если указаны даты (например: с 1 мая по 10 июня), добавь фильтр `a.date BETWEEN ... AND ...`
5. "Список студентов группы ФКС-11" → найти `groups.id` по `groups.name`, затем показать `students.full_name`, где `students.group_id = groups.id`.

Выводи только корректный SQL-запрос. Никаких пояснений.
"""



    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        temperature=0.1,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": prompt}
        ]
    )
    return response.choices[0].message.content.strip()


def extract_dates_from_text(text):
    matches = re.findall(r'\b(?:с|от|c|между)?\s*([А-Яа-яA-Za-z0-9\s.,]+?)\s*(?:по|до|и|по|до)?\s*([А-Яа-яA-Za-z0-9\s.,]*)\b', text)
    for match in matches:
        start_raw, end_raw = match
        start = dateparser.parse(start_raw)
        end = dateparser.parse(end_raw) if end_raw else None
        if start:
            return start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d') if end else None
    return None, None

@app.route('/ai-chat', methods=['POST'])
def ai_chat():
    if session.get('role') != 'auditor':
        return jsonify({'reply': 'Доступ разрешён только аудитору.'}), 403

    db = get_db()
    user_input = request.json.get('message', '').strip()
    if not user_input:
        return jsonify({'reply': 'Пустой запрос.'})

    try:
        # Попытка вытащить естественные даты из текста
        start_date, end_date = extract_dates_from_text(user_input)
        if start_date and end_date:
            user_input += f"\nДата начала: {start_date}, дата конца: {end_date}"

        sql_query = generate_sql_from_prompt(user_input)
        cursor = db.execute(sql_query)
        rows = cursor.fetchall()

        if not rows:
            return jsonify({'reply': 'Ничего не найдено.'})

        header = rows[0].keys()
        table_html = "<table class='table table-sm table-bordered mt-2'><thead><tr>"
        for col in header:
            table_html += f"<th>{col}</th>"
        table_html += "</tr></thead><tbody>"
        for row in rows:
            table_html += "<tr>"
            for col in header:
                table_html += f"<td>{row[col]}</td>"
            table_html += "</tr>"
        table_html += "</tbody></table>"

        return jsonify({
            'reply': 'Вот, что мне удалось найти:',
            'reply_html': table_html
        })

    except Exception as e:
        return jsonify({'reply': f'Ошибка: {str(e)}'})

@app.route('/ai/full')
def ai_full_page():
    if session.get('role') != 'auditor':
        return redirect('/login')
    return render_template('ai_full.html')


if __name__ == '__main__':
    app.run(debug=True)

