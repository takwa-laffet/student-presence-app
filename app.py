import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import psycopg
from flask import Flask, flash, redirect, render_template, request, url_for, send_file, jsonify
from flask_wtf.csrf import CSRFProtect
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from sqlalchemy import or_, text
from sqlalchemy.exc import IntegrityError

from config import Config
from forms import EleveForm, FormationForm, PresenceForm
from models import Eleve, Formation, Presence, Salary, db, eleve_formations


app = Flask(__name__)
app.config.from_object(Config)
csrf = CSRFProtect(app)

db.init_app(app)


def parse_date_arg(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_time_arg(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def create_presence_chart(presences_by_eleve, eleves, output_path):
    if not presences_by_eleve:
        return None
    
    eleve_names = [e.nom_complet for e in eleves if e.id in presences_by_eleve]
    hours = [sum(p.duree_heures for p in presences_by_eleve[e.id]) for e in eleves if e.id in presences_by_eleve]
    
    if not eleve_names:
        return None
    
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(eleve_names, hours, color='#0d7a66')
    ax.set_xlabel('Eleve')
    ax.set_ylabel('Heures')
    ax.set_title('Heures de formation par eleve')
    ax.tick_params(axis='x', rotation=45)
    
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height, f'{height:.1f}h', ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()
    return output_path


def create_formation_chart(presences, output_path):
    if not presences:
        return None
    
    formation_hours = defaultdict(float)
    for p in presences:
        formation_hours[p.formation.nom_formation] += p.duree_heures
    
    if not formation_hours:
        return None
    
    fig, ax = plt.subplots(figsize=(8, 6))
    names = list(formation_hours.keys())
    hours = list(formation_hours.values())
    
    colors_list = plt.cm.Set3(range(len(names)))
    wedges, texts, autotexts = ax.pie(hours, labels=names, autopct='%1.1f%%', colors=colors_list, startangle=90)
    ax.set_title('Heures par formation')
    
    plt.setp(autotexts, size=8, weight="bold")
    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()
    return output_path


def normalize_eleve_ids(raw_ids):
    if not isinstance(raw_ids, list):
        return []
    cleaned = []
    for value in raw_ids:
        try:
            cleaned.append(int(value))
        except (TypeError, ValueError):
            continue
    return cleaned


def iter_weekdays(start_date, end_date):
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            yield current
        current += timedelta(days=1)


def ensure_database_exists():
    with psycopg.connect(
        host=app.config["DB_HOST"],
        port=int(app.config["DB_PORT"]),
        user=app.config["DB_USER"],
        password=app.config["DB_PASSWORD"],
        dbname="postgres",
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (app.config["DB_NAME"],))
            exists = cursor.fetchone() is not None
            if not exists:
                db_name = app.config["DB_NAME"].replace('"', '""')
                cursor.execute(f'CREATE DATABASE "{db_name}"')


def ensure_schema_compatibility():
    with db.engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS eleve_formations (
                    eleve_id INTEGER NOT NULL REFERENCES eleves(id) ON DELETE CASCADE,
                    formation_id INTEGER NOT NULL REFERENCES formations(id) ON DELETE CASCADE,
                    PRIMARY KEY (eleve_id, formation_id)
                )
                """
            )
        )

        has_legacy_formation_id = connection.execute(
            text(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'eleves'
                  AND column_name = 'formation_id'
                """
            )
        ).first()
        if has_legacy_formation_id:
            connection.execute(
                text(
                    """
                    INSERT INTO eleve_formations (eleve_id, formation_id)
                    SELECT id, formation_id
                    FROM eleves
                    WHERE formation_id IS NOT NULL
                    ON CONFLICT DO NOTHING
                    """
                )
            )

        connection.execute(
            text(
                """
                INSERT INTO eleve_formations (eleve_id, formation_id)
                SELECT DISTINCT eleve_id, formation_id
                FROM presences
                ON CONFLICT DO NOTHING
                """
            )
        )

        has_old_presence_constraint = connection.execute(
            text(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_presence_eleve_date'
                """
            )
        ).first()
        if has_old_presence_constraint:
            connection.execute(text("ALTER TABLE presences DROP CONSTRAINT uq_presence_eleve_date"))

        has_new_presence_constraint = connection.execute(
            text(
                """
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_presence_eleve_formation_date'
                """
            )
        ).first()
        if not has_new_presence_constraint:
            connection.execute(
                text(
                    """
                    ALTER TABLE presences
                    ADD CONSTRAINT uq_presence_eleve_formation_date
                    UNIQUE (eleve_id, formation_id, date)
                    """
                )
            )


def seed_initial_data():
    formation_specs = [
        {
            "nom_formation": "dev web",
            "description": "Formation developpement web",
            "total_duration_hours": 40,
            "session_duration_hours": 2,
        },
        {
            "nom_formation": "Python",
            "description": "Formation Python",
            "total_duration_hours": 40,
            "session_duration_hours": 2,
        },
    ]

    formations_by_name = {}
    for spec in formation_specs:
        formation = Formation.query.filter_by(nom_formation=spec["nom_formation"]).first()
        if not formation:
            formation = Formation(**spec)
            db.session.add(formation)
            db.session.flush()
        formations_by_name[spec["nom_formation"]] = formation

    student_specs = [
        {
            "nom": "Benothmen",
            "prenom": "Hafiza",
            "email": "benothmenhafiza@gmail.com",
            "numero": "+966 54 955 8601",
            "formations": ["dev web"],
        },
        {
            "nom": "Hafsi",
            "prenom": "Nourhenne",
            "email": "hafsinour97@gmail.com",
            "numero": "+216 94 260 794",
            "formations": ["Python"],
        },
        {
            "nom": "Raissi",
            "prenom": "Mariem",
            "email": "mariem.raissi1190@gmail.com",
            "numero": "+216 20 720 262",
            "formations": ["dev web"],
        },
    ]

    for spec in student_specs:
        eleve = Eleve.query.filter_by(email=spec["email"]).first()
        target_formations = [formations_by_name[name] for name in spec["formations"] if name in formations_by_name]

        if not eleve:
            eleve = Eleve(
                nom=spec["nom"],
                prenom=spec["prenom"],
                email=spec["email"],
                numero=spec["numero"],
            )
            eleve.formations = target_formations
            db.session.add(eleve)
            continue

        eleve.nom = spec["nom"]
        eleve.prenom = spec["prenom"]
        eleve.numero = spec["numero"]
        eleve.formations = target_formations

    presence_specs = [
        {
            "eleve_email": "benothmenhafiza@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-07", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("11:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("14:00", "%H:%M").time(),
        },
        {
            "eleve_email": "benothmenhafiza@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-08", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("11:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("13:00", "%H:%M").time(),
        },
        {
            "eleve_email": "benothmenhafiza@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-23", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("19:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("21:00", "%H:%M").time(),
        },
        {
            "eleve_email": "benothmenhafiza@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-27", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("10:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("12:00", "%H:%M").time(),
        },
        {
            "eleve_email": "benothmenhafiza@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-28", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("08:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("10:00", "%H:%M").time(),
        },
        {
            "eleve_email": "benothmenhafiza@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-29", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("18:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("20:00", "%H:%M").time(),
        },
        {
            "eleve_email": "hafsinour97@gmail.com",
            "formation_name": "Python",
            "date": datetime.strptime("2026-03-01", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("10:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("12:00", "%H:%M").time(),
        },
        {
            "eleve_email": "hafsinour97@gmail.com",
            "formation_name": "Python",
            "date": datetime.strptime("2026-03-15", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("10:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("12:00", "%H:%M").time(),
        },
        {
            "eleve_email": "hafsinour97@gmail.com",
            "formation_name": "Python",
            "date": datetime.strptime("2026-03-29", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("10:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("14:00", "%H:%M").time(),
        },
        {
            "eleve_email": "mariem.raissi1190@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-07", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("11:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("14:00", "%H:%M").time(),
        },
        {
            "eleve_email": "mariem.raissi1190@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-08", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("11:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("13:00", "%H:%M").time(),
        },
        {
            "eleve_email": "mariem.raissi1190@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-23", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("19:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("21:00", "%H:%M").time(),
        },
        {
            "eleve_email": "hafsinour97@gmail.com",
            "formation_name": "Python",
            "date": datetime.strptime("2026-03-31", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("19:22", "%H:%M").time(),
            "heure_fin": datetime.strptime("21:22", "%H:%M").time(),
        },
        {
            "eleve_email": "benothmenhafiza@gmail.com",
            "formation_name": "dev web",
            "date": datetime.strptime("2026-03-31", "%Y-%m-%d").date(),
            "heure_debut": datetime.strptime("16:00", "%H:%M").time(),
            "heure_fin": datetime.strptime("18:00", "%H:%M").time(),
        },
    ]

    for spec in presence_specs:
        eleve = Eleve.query.filter_by(email=spec["eleve_email"]).first()
        formation = Formation.query.filter_by(nom_formation=spec["formation_name"]).first()

        if eleve and formation:
            existing = Presence.query.filter_by(
                eleve_id=eleve.id,
                formation_id=formation.id,
                date=spec["date"],
            ).first()

            if not existing:
                presence = Presence(
                    eleve_id=eleve.id,
                    formation_id=formation.id,
                    date=spec["date"],
                    heure_debut=spec["heure_debut"],
                    heure_fin=spec["heure_fin"],
                )
                db.session.add(presence)

    db.session.commit()


@app.cli.command("seed-data")
def seed_data_command():
    ensure_database_exists()
    db.create_all()
    ensure_schema_compatibility()
    seed_initial_data()
    print("Seed data inserted/updated successfully.")


@app.context_processor
def inject_current_year():
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    all_presences = Presence.query.all()
    week_presences = Presence.query.filter(
        Presence.date >= monday,
        Presence.date <= sunday,
    ).all()

    total_heures_global = round(sum(p.duree_heures for p in all_presences), 2)
    total_heures_semaine = round(sum(p.duree_heures for p in week_presences), 2)

    return {
        "current_year": datetime.now().year,
        "total_heures_global": total_heures_global,
        "total_heures_semaine": total_heures_semaine,
    }


@app.template_filter("date_input")
def date_input_filter(value):
    if not value:
        return ""
    return value.strftime("%Y-%m-%d")


with app.app_context():
    ensure_database_exists()
    db.create_all()
    ensure_schema_compatibility()
    # seed_initial_data()  # Commented out to avoid auto-seeding


@app.route("/")
def dashboard():
    today = date.today()
    current_iso_year, current_iso_week, _ = today.isocalendar()

    selected_week = request.args.get("week", type=int, default=current_iso_week)
    selected_year = request.args.get("year", type=int, default=current_iso_year)

    start_of_week = date.fromisocalendar(selected_year, selected_week, 1)
    end_of_week = start_of_week + timedelta(days=6)

    presences_week = (
        Presence.query.filter(Presence.date >= start_of_week, Presence.date <= end_of_week)
        .join(Eleve)
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )

    totals = defaultdict(float)
    for p in presences_week:
        totals[p.eleve.nom_complet] += p.duree_heures

    eleves_count = Eleve.query.count()
    formations_count = Formation.query.count()
    presences_count = Presence.query.count()

    return render_template(
        "dashboard.html",
        eleves_count=eleves_count,
        formations_count=formations_count,
        presences_count=presences_count,
        totals=sorted(totals.items(), key=lambda x: x[0]),
        selected_week=selected_week,
        selected_year=selected_year,
        start_of_week=start_of_week,
        end_of_week=end_of_week,
    )


@app.route("/calendar")
def calendar_view():
    today = date.today()
    year = request.args.get("year", type=int, default=today.year)
    month = request.args.get("month", type=int, default=today.month)

    if month < 1 or month > 12:
        month = today.month
    if year < 1:
        year = today.year

    from calendar import monthrange

    last_day = monthrange(year, month)[1]
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    presences = (
        Presence.query.join(Eleve).join(Formation)
        .filter(Presence.date >= start_date, Presence.date <= end_date)
        .order_by(Presence.date.asc(), Presence.heure_debut.asc())
        .all()
    )

    previous_month_date = start_date - timedelta(days=1)
    next_month_date = end_date + timedelta(days=1)
    month_names = [
        "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
    ]

    return render_template(
        "calendar.html",
        presences=presences,
        calendar_month=month,
        calendar_year=year,
        calendar_month_label=f"{month_names[month - 1]} {year}",
        previous_month=previous_month_date.month,
        previous_year=previous_month_date.year,
        next_month=next_month_date.month,
        next_year=next_month_date.year,
        today_month=today.month,
        today_year=today.year,
    )


@app.route("/eleves", methods=["GET", "POST"])
def eleves():
    form = EleveForm()
    formations = Formation.query.order_by(Formation.nom_formation.asc()).all()
    form.formation_ids.choices = [(f.id, f.nom_formation) for f in formations]

    if form.validate_on_submit():
        selected_ids = [fid for fid in (form.formation_ids.data or []) if fid]
        selected_formations = Formation.query.filter(Formation.id.in_(selected_ids)).all() if selected_ids else []

        eleve = Eleve(
            nom=form.nom.data.strip(),
            prenom=form.prenom.data.strip(),
            email=form.email.data.strip().lower(),
            numero=(form.numero.data or "").strip() or None,
        )
        eleve.formations = selected_formations
        db.session.add(eleve)

        try:
            db.session.commit()
            flash("Eleve ajoute avec succes.", "success")
            return redirect(url_for("eleves"))
        except IntegrityError:
            db.session.rollback()
            flash("Cet email existe deja.", "danger")

    search = (request.args.get("q") or "").strip()
    query = Eleve.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Eleve.nom.ilike(like),
                Eleve.prenom.ilike(like),
                Eleve.email.ilike(like),
                Eleve.numero.ilike(like),
            )
        )

    all_eleves = query.order_by(Eleve.nom.asc(), Eleve.prenom.asc()).all()
    return render_template("eleves.html", form=form, eleves=all_eleves, search=search)


@app.route("/eleve/edit/<int:id>", methods=["GET", "POST"])
def eleve_edit(id):
    eleve = Eleve.query.get_or_404(id)
    form = EleveForm(obj=eleve)
    formations = Formation.query.order_by(Formation.nom_formation.asc()).all()
    form.formation_ids.choices = [(f.id, f.nom_formation) for f in formations]

    current_formations = sorted(eleve.formations, key=lambda item: item.nom_formation.lower())
    if request.method == "GET":
        form.formation_ids.data = [f.id for f in current_formations]

    if form.validate_on_submit():
        selected_ids = [fid for fid in (form.formation_ids.data or []) if fid]
        selected_formations = Formation.query.filter(Formation.id.in_(selected_ids)).all() if selected_ids else []

        eleve.nom = form.nom.data.strip()
        eleve.prenom = form.prenom.data.strip()
        eleve.email = form.email.data.strip().lower()
        eleve.numero = (form.numero.data or "").strip() or None
        eleve.formations = selected_formations

        try:
            db.session.commit()
            flash("Eleve modifie avec succes.", "success")
            return redirect(url_for("eleves"))
        except IntegrityError:
            db.session.rollback()
            flash("Cet email existe deja.", "danger")

    return render_template("eleve_form.html", form=form, eleve=eleve, current_formations=current_formations)


@app.route("/eleve/delete/<int:id>", methods=["POST"])
def eleve_delete(id):
    eleve = Eleve.query.get_or_404(id)
    db.session.delete(eleve)
    db.session.commit()
    flash("Eleve supprime avec succes.", "success")
    return redirect(url_for("eleves"))


@app.route("/formations", methods=["GET", "POST"])
def formations():
    form = FormationForm()

    if form.validate_on_submit():
        formation = Formation(
            nom_formation=form.nom_formation.data.strip(),
            description=(form.description.data or "").strip(),
            total_duration_hours=form.total_duration_hours.data,
            session_duration_hours=form.session_duration_hours.data,
        )
        db.session.add(formation)
        db.session.commit()
        flash("Formation ajoutee avec succes.", "success")
        return redirect(url_for("formations"))

    search = (request.args.get("q") or "").strip()
    query = Formation.query
    if search:
        like = f"%{search}%"
        query = query.filter(
            or_(
                Formation.nom_formation.ilike(like),
                Formation.description.ilike(like),
            )
        )

    all_formations = query.order_by(Formation.nom_formation.asc()).all()
    formation_ids = [f.id for f in all_formations]
    realised_by_formation = defaultdict(float)
    seen_slots_by_formation = defaultdict(set)

    if formation_ids:
        formation_presences = Presence.query.filter(Presence.formation_id.in_(formation_ids)).all()
        for item in formation_presences:
            slot = (item.date, item.heure_debut, item.heure_fin)
            if slot in seen_slots_by_formation[item.formation_id]:
                continue
            seen_slots_by_formation[item.formation_id].add(slot)
            realised_by_formation[item.formation_id] += item.duree_heures

    formation_stats = {
        f.id: {
            "realised": round(realised_by_formation.get(f.id, 0.0), 2),
            "remaining": round(
                max(float(f.total_duration_hours or 0) - realised_by_formation.get(f.id, 0.0), 0),
                2,
            ),
        }
        for f in all_formations
    }

    return render_template(
        "formations.html",
        form=form,
        formations=all_formations,
        formation_stats=formation_stats,
        search=search,
    )


@app.route("/formation/edit/<int:id>", methods=["GET", "POST"])
def formation_edit(id):
    formation = Formation.query.get_or_404(id)
    form = FormationForm(obj=formation)

    if form.validate_on_submit():
        formation.nom_formation = form.nom_formation.data.strip()
        formation.description = (form.description.data or "").strip()
        formation.total_duration_hours = form.total_duration_hours.data
        formation.session_duration_hours = form.session_duration_hours.data
        db.session.commit()
        flash("Formation modifiee avec succes.", "success")
        return redirect(url_for("formations"))

    return render_template("formation_form.html", form=form, formation=formation)


@app.route("/formation/delete/<int:id>", methods=["POST"])
def formation_delete(id):
    formation = Formation.query.get_or_404(id)
    db.session.delete(formation)
    db.session.commit()
    flash("Formation supprimee avec succes.", "success")
    return redirect(url_for("formations"))


@app.route("/formation/<int:formation_id>")
def formation_details(formation_id):
    formation = Formation.query.get_or_404(formation_id)
    
    eleves = sorted(formation.eleves, key=lambda e: e.nom_complet.lower())
    presences = (
        Presence.query.filter_by(formation_id=formation_id)
        .join(Eleve)
        .order_by(Presence.date.desc(), Presence.heure_debut.desc())
        .all()
    )
    
    total_realised = formation.realised_duration_hours
    remaining = formation.remaining_duration_hours
    progress_percentage = (total_realised / formation.total_duration_hours * 100) if formation.total_duration_hours > 0 else 0
    
    unique_slots = {}
    for p in presences:
        slot = (p.date, p.heure_debut, p.heure_fin)
        if slot not in unique_slots:
            unique_slots[slot] = p.duree_heures
    
    salary_rate = 15
    total_salary = sum(unique_slots.values()) * salary_rate
    
    presences_by_eleve = defaultdict(float)
    presence_count_by_eleve = defaultdict(int)
    seen_slots_by_eleve = defaultdict(set)
    for p in presences:
        slot = (p.date, p.heure_debut, p.heure_fin)
        if slot in seen_slots_by_eleve[p.eleve_id]:
            continue
        seen_slots_by_eleve[p.eleve_id].add(slot)
        presences_by_eleve[p.eleve_id] += p.duree_heures
        presence_count_by_eleve[p.eleve_id] += 1
    
    eleves_stats = []
    for eleve in eleves:
        hours = presences_by_eleve.get(eleve.id, 0)
        count = presence_count_by_eleve.get(eleve.id, 0)
        progress = (hours / total_realised * 100) if total_realised > 0 else 0
        eleves_stats.append({
            "eleve": eleve,
            "total_hours": hours,
            "presence_count": count,
            "progress": min(progress, 100)
        })
    
    recent_presences = presences[:20]
    
    month_hours = defaultdict(float)
    week_hours = defaultdict(float)
    day_counts = defaultdict(int)
    for p in presences:
        month_key = p.date.strftime("%Y-%m")
        month_hours[month_key] += p.duree_heures
        iso_cal = p.date.isocalendar()
        week_key = f"{iso_cal[0]}-S{iso_cal[1]:02d}"
        week_hours[week_key] += p.duree_heures
        day_counts[p.date] += 1
    
    sorted_months = sorted(month_hours.keys())
    total_by_month = [{"month": m, "hours": month_hours[m]} for m in sorted_months]
    
    sorted_weeks = sorted(week_hours.keys())
    total_by_week = [{"week": w, "hours": week_hours[w]} for w in sorted_weeks]
    
    chart_eleve_url = None
    chart_month_url = None
    chart_week_url = None
    chart_days_url = None
    
    if presences_by_eleve:
        chart_eleve_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), f'formation_{formation_id}_eleve.png')
        if create_presence_chart_for_formation(presences_by_eleve, eleves, chart_eleve_path):
            chart_eleve_url = url_for('serve_temp_image', filename=os.path.basename(chart_eleve_path))
    
    if month_hours:
        month_chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), f'formation_{formation_id}_month.png')
        if create_month_chart(month_hours, month_chart_path):
            chart_month_url = url_for('serve_temp_image', filename=os.path.basename(month_chart_path))
    
    if week_hours:
        week_chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), f'formation_{formation_id}_week.png')
        if create_week_chart(week_hours, week_chart_path):
            chart_week_url = url_for('serve_temp_image', filename=os.path.basename(week_chart_path))
    
    if day_counts:
        day_chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), f'formation_{formation_id}_days.png')
        if create_day_presence_chart(day_counts, day_chart_path):
            chart_days_url = url_for('serve_temp_image', filename=os.path.basename(day_chart_path))
    
    return render_template(
        "formation_details.html",
        formation=formation,
        eleves=eleves,
        presences=presences,
        eleves_stats=eleves_stats,
        recent_presences=recent_presences,
        realised_hours=total_realised,
        remaining_hours=remaining,
        progress_percentage=progress_percentage,
        total_salary=total_salary,
        chart_eleve_url=chart_eleve_url,
        chart_month_url=chart_month_url,
        chart_week_url=chart_week_url,
        chart_days_url=chart_days_url,
        total_by_month=total_by_month,
        total_by_week=total_by_week,
    )


def create_presence_chart_for_formation(presences_by_eleve, eleves, output_path):
    if not presences_by_eleve:
        return None
    
    eleve_names = [e.nom_complet for e in eleves if e.id in presences_by_eleve]
    hours = [presences_by_eleve.get(e.id, 0) for e in eleves if e.id in presences_by_eleve]
    
    if not eleve_names:
        return None
    
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(eleve_names, hours, color='#0d7a66')
    ax.set_xlabel('Eleve')
    ax.set_ylabel('Heures')
    ax.set_title('Heures de formation par eleve')
    ax.tick_params(axis='x', rotation=45)
    
    for bar in bars:
        height = bar.get_height()
        if height > 0:
            ax.text(bar.get_x() + bar.get_width()/2., height, f'{height:.1f}h', ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()
    return output_path


def create_month_chart(month_hours, output_path):
    if not month_hours:
        return None
    
    months = sorted(month_hours.keys())
    hours = [month_hours[m] for m in months]
    
    fig, ax = plt.subplots(figsize=(8, 5))
    bars = ax.bar(months, hours, color='#17a2b8')
    ax.set_xlabel('Mois')
    ax.set_ylabel('Heures')
    ax.set_title('Heures par mois')
    ax.tick_params(axis='x', rotation=45)
    
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height, f'{height:.1f}h', ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()
    return output_path


def create_week_chart(week_hours, output_path):
    if not week_hours:
        return None
    
    weeks = sorted(week_hours.keys())
    hours = [week_hours[w] for w in weeks]
    
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(weeks, hours, color='#ffc107')
    ax.set_xlabel('Semaine')
    ax.set_ylabel('Heures')
    ax.set_title('Heures par semaine')
    ax.tick_params(axis='x', rotation=45)
    
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height, f'{height:.1f}h', ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()
    return output_path


def create_day_presence_chart(day_counts, output_path):
    if not day_counts:
        return None
    
    sorted_days = sorted(day_counts.keys())
    counts = [day_counts[d] for d in sorted_days]
    labels = [d.strftime('%d/%m') for d in sorted_days]
    
    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(labels, counts, color='#28a745')
    ax.set_xlabel('Date')
    ax.set_ylabel('Nombre de presences')
    ax.set_title('Nombre de presences par jour')
    ax.tick_params(axis='x', rotation=45)
    
    for bar in bars:
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height, f'{int(height)}', ha='center', va='bottom', fontsize=8)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=100)
    plt.close()
    return output_path


@app.route("/temp/<path:filename>")
def serve_temp_image(filename):
    temp_folder = app.config.get('TEMP_FOLDER', '/tmp')
    return send_file(os.path.join(temp_folder, filename))


@app.route("/presence", methods=["GET", "POST"])
def presence():
    form = PresenceForm()

    eleves = Eleve.query.order_by(Eleve.nom.asc(), Eleve.prenom.asc()).all()
    formations = Formation.query.order_by(Formation.nom_formation.asc()).all()

    form.eleve_id.choices = [(e.id, e.nom_complet) for e in eleves]
    form.formation_id.choices = [(f.id, f.nom_formation) for f in formations]

    if request.method == "GET" and not form.date.data:
        form.date.data = date.today()

    if form.validate_on_submit():
        if form.heure_fin.data <= form.heure_debut.data:
            flash("L'heure de fin doit etre superieure a l'heure de debut.", "danger")
            return redirect(url_for("presence"))

        eleve = Eleve.query.get(form.eleve_id.data)
        if eleve and not any(f.id == form.formation_id.data for f in eleve.formations):
            target_formation = Formation.query.get(form.formation_id.data)
            if target_formation:
                eleve.formations.append(target_formation)

        exists = Presence.query.filter_by(
            eleve_id=form.eleve_id.data,
            formation_id=form.formation_id.data,
            date=form.date.data,
        ).first()

        if exists:
            flash(
                "Doublon detecte: cet eleve a deja une presence pour cette formation et cette date.",
                "danger",
            )
            return redirect(url_for("presence"))

        item = Presence(
            eleve_id=form.eleve_id.data,
            formation_id=form.formation_id.data,
            date=form.date.data,
            heure_debut=form.heure_debut.data,
            heure_fin=form.heure_fin.data,
        )
        db.session.add(item)
        db.session.commit()
        flash("Presence ajoutee avec succes.", "success")
        return redirect(url_for("presence"))

    query = Presence.query.join(Eleve).join(Formation)

    filter_eleve_id = request.args.get("eleve_id", type=int)
    filter_date = parse_date_arg(request.args.get("date"))

    if filter_eleve_id:
        query = query.filter(Presence.eleve_id == filter_eleve_id)
    if filter_date:
        query = query.filter(Presence.date == filter_date)

    presences = query.order_by(Presence.date.desc(), Presence.heure_debut.desc()).all()

    attendance_statuses = []
    if filter_date:
        status_eleves = [e for e in eleves if not filter_eleve_id or e.id == filter_eleve_id]
        present_ids = {
            p.eleve_id
            for p in Presence.query.filter(Presence.date == filter_date).all()
        }
        attendance_statuses = [
            {
                "eleve": e,
                "status": "Present" if e.id in present_ids else "Absent",
            }
            for e in status_eleves
        ]

    return render_template(
        "presence.html",
        form=form,
        presences=presences,
        eleves=eleves,
        formations=formations,
        today=date.today(),
        yesterday=date.today() - timedelta(days=1),
        filter_eleve_id=filter_eleve_id,
        filter_date=filter_date,
        attendance_statuses=attendance_statuses,
    )


@app.route("/presence/edit/<int:id>", methods=["GET", "POST"])
def presence_edit(id):
    item = Presence.query.get_or_404(id)
    form = PresenceForm(obj=item)

    eleves = Eleve.query.order_by(Eleve.nom.asc(), Eleve.prenom.asc()).all()
    formations = Formation.query.order_by(Formation.nom_formation.asc()).all()

    form.eleve_id.choices = [(e.id, e.nom_complet) for e in eleves]
    form.formation_id.choices = [(f.id, f.nom_formation) for f in formations]

    if form.validate_on_submit():
        if form.heure_fin.data <= form.heure_debut.data:
            flash("L'heure de fin doit etre superieure a l'heure de debut.", "danger")
            return redirect(url_for("presence_edit", id=item.id))

        eleve = Eleve.query.get(form.eleve_id.data)
        if eleve and not any(f.id == form.formation_id.data for f in eleve.formations):
            target_formation = Formation.query.get(form.formation_id.data)
            if target_formation:
                eleve.formations.append(target_formation)

        exists = Presence.query.filter(
            Presence.eleve_id == form.eleve_id.data,
            Presence.formation_id == form.formation_id.data,
            Presence.date == form.date.data,
            Presence.id != item.id,
        ).first()

        if exists:
            flash(
                "Doublon detecte: cet eleve a deja une presence pour cette formation et cette date.",
                "danger",
            )
            return redirect(url_for("presence_edit", id=item.id))

        item.eleve_id = form.eleve_id.data
        item.formation_id = form.formation_id.data
        item.date = form.date.data
        item.heure_debut = form.heure_debut.data
        item.heure_fin = form.heure_fin.data

        db.session.commit()
        flash("Presence modifiee avec succes.", "success")
        return redirect(url_for("presence"))

    return render_template(
        "presence_form.html",
        form=form,
        item=item,
    )


@app.route("/api/formation/eleves/<int:formation_id>", methods=["GET"])
def api_formation_eleves(formation_id):
    formation = Formation.query.get_or_404(formation_id)
    presence_date = parse_date_arg(request.args.get("presence_date")) or date.today()
    eleves = (
        Eleve.query.join(eleve_formations, Eleve.id == eleve_formations.c.eleve_id)
        .filter(eleve_formations.c.formation_id == formation_id)
        .order_by(Eleve.nom.asc(), Eleve.prenom.asc())
        .all()
    )
    
    eleves_data = []
    for eleve in eleves:
        presence_today = Presence.query.filter_by(
            eleve_id=eleve.id, 
            formation_id=formation_id,
            date=presence_date
        ).first()
        
        eleves_data.append({
            "id": eleve.id,
            "nom_complet": eleve.nom_complet,
            "has_presence": presence_today is not None
        })
    
    return jsonify({
        "eleves": eleves_data,
        "session_duration_hours": formation.session_duration_hours
    })


@app.route("/api/presence/bulk_create", methods=["POST"])
def api_presence_bulk_create():
    data = request.get_json(silent=True) or {}

    try:
        formation_id = int(data.get("formation_id"))
    except (TypeError, ValueError, AttributeError):
        return jsonify({"added": 0, "skipped": 0, "errors": ["formation_id invalide"]}), 400

    eleve_ids = normalize_eleve_ids(data.get("eleve_ids", []))
    if not eleve_ids:
        return jsonify({"added": 0, "skipped": 0, "errors": ["Aucun eleve selectionne"]}), 400

    presence_date = parse_date_arg(data.get("presence_date")) or date.today()
    heure_debut = parse_time_arg(data.get("heure_debut")) or datetime.strptime("09:00", "%H:%M").time()
    heure_fin = parse_time_arg(data.get("heure_fin")) or datetime.strptime("12:00", "%H:%M").time()

    if heure_fin <= heure_debut:
        return jsonify({"added": 0, "skipped": 0, "errors": ["L'heure de fin doit etre superieure a l'heure de debut"]}), 400
    
    formation = Formation.query.get_or_404(formation_id)
    
    added = 0
    skipped = 0
    errors = []
    
    for eleve_id in eleve_ids:
        eleve = Eleve.query.get(eleve_id)
        if not eleve:
            skipped += 1
            continue

        if not any(f.id == formation_id for f in eleve.formations):
            eleve.formations.append(formation)

        try:
            existing = Presence.query.filter_by(
                eleve_id=eleve_id,
                formation_id=formation_id,
                date=presence_date
            ).first()
            
            if not existing:
                presence = Presence(
                    eleve_id=eleve_id,
                    formation_id=formation_id,
                    date=presence_date,
                    heure_debut=heure_debut,
                    heure_fin=heure_fin,
                )
                db.session.add(presence)
                added += 1
            else:
                skipped += 1
        except Exception as e:
            errors.append(str(e))
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        errors.append(str(e))

    return jsonify({"added": added, "skipped": skipped, "errors": errors})


@app.route("/rapport", methods=["GET"])
def rapport():
    today = date.today()
    first_of_month = today.replace(day=1)

    start_date = parse_date_arg(request.args.get("start_date")) or first_of_month
    end_date = parse_date_arg(request.args.get("end_date")) or today
    selected_eleve_id = request.args.get("eleve_id", type=int)
    selected_formation_id = request.args.get("formation_id", type=int)

    if end_date < start_date:
        flash("La date de fin doit etre superieure ou egale a la date de debut.", "danger")
        return redirect(
            url_for(
                "rapport",
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=start_date.strftime("%Y-%m-%d"),
                eleve_id=selected_eleve_id,
            )
        )

    eleves_query = Eleve.query.order_by(Eleve.nom.asc(), Eleve.prenom.asc())
    if selected_eleve_id:
        eleves_query = eleves_query.filter(Eleve.id == selected_eleve_id)
    eleves = eleves_query.all()

    presences_query = Presence.query.filter(
        Presence.date >= start_date,
        Presence.date <= end_date,
    )
    if selected_eleve_id:
        presences_query = presences_query.filter(Presence.eleve_id == selected_eleve_id)
    if selected_formation_id:
        presences_query = presences_query.filter(Presence.formation_id == selected_formation_id)

    presences = (
        presences_query.join(Eleve)
        .join(Formation)
        .order_by(Presence.date.asc(), Presence.heure_debut.asc())
        .all()
    )

    workdays = list(iter_weekdays(start_date, end_date))
    workdays_set = set(workdays)

    presences_by_eleve = defaultdict(list)
    present_days_by_eleve = defaultdict(set)
    for p in presences:
        presences_by_eleve[p.eleve_id].append(p)
        present_days_by_eleve[p.eleve_id].add(p.date)

    rows = []
    for e in eleves:
        items = presences_by_eleve.get(e.id, [])
        total_heures = round(sum(item.duree_heures for item in items), 2)
        jours_presents = len(present_days_by_eleve.get(e.id, set()))
        absences = sorted(workdays_set - present_days_by_eleve.get(e.id, set()))
        
        # Calculate realised hours per linked formation for this student
        formation_realised_hours = defaultdict(float)
        for p in items:
            formation_realised_hours[p.formation_id] += p.duree_heures

        assigned_formations = sorted(e.formations, key=lambda f: f.nom_formation.lower())
        formation_stats = []
        for formation in assigned_formations:
            realised_hours = round(formation_realised_hours.get(formation.id, 0), 2)
            formation_stats.append(
                {
                    "formation": formation,
                    "realised_hours": realised_hours,
                    "remaining_hours": max(formation.total_duration_hours - realised_hours, 0),
                }
            )

        rows.append(
            {
                "eleve": e,
                "items": items,
                "total_heures": total_heures,
                "jours_presents": jours_presents,
                "jours_absents": len(absences),
                "dates_absence": absences,
                "formation_realised_hours": dict(formation_realised_hours),
                "formation_stats": formation_stats,
            }
        )

    total_presence_records = len(presences)
    total_heures_global = round(sum(p.duree_heures for p in presences), 2)

    return render_template(
        "rapport.html",
        rows=rows,
        eleves_all=Eleve.query.order_by(Eleve.nom.asc(), Eleve.prenom.asc()).all(),
        formations_all=Formation.query.order_by(Formation.nom_formation.asc()).all(),
        selected_eleve_id=selected_eleve_id,
        selected_formation_id=selected_formation_id,
        start_date=start_date,
        end_date=end_date,
        today=today,
        total_presence_records=total_presence_records,
        total_heures_global=total_heures_global,
        total_workdays=len(workdays),
    )


@app.route("/rapport/pdf", methods=["GET"])
def rapport_pdf():
    today = date.today()
    first_of_month = today.replace(day=1)

    start_date = parse_date_arg(request.args.get("start_date")) or first_of_month
    end_date = parse_date_arg(request.args.get("end_date")) or today
    selected_eleve_id = request.args.get("eleve_id", type=int)
    selected_formation_id = request.args.get("formation_id", type=int)

    eleves_query = Eleve.query.order_by(Eleve.nom.asc(), Eleve.prenom.asc())
    if selected_eleve_id:
        eleves_query = eleves_query.filter(Eleve.id == selected_eleve_id)
    eleves = eleves_query.all()

    presences_query = Presence.query.filter(
        Presence.date >= start_date,
        Presence.date <= end_date,
    )
    if selected_eleve_id:
        presences_query = presences_query.filter(Presence.eleve_id == selected_eleve_id)
    if selected_formation_id:
        presences_query = presences_query.filter(Presence.formation_id == selected_formation_id)

    presences = (
        presences_query.join(Eleve)
        .join(Formation)
        .order_by(Presence.date.asc(), Presence.heure_debut.asc())
        .all()
    )

    workdays = list(iter_weekdays(start_date, end_date))
    workdays_set = set(workdays)

    presences_by_eleve = defaultdict(list)
    present_days_by_eleve = defaultdict(set)
    for p in presences:
        presences_by_eleve[p.eleve_id].append(p)
        present_days_by_eleve[p.eleve_id].add(p.date)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=18,
        textColor=colors.HexColor("#0d7a66"),
        spaceAfter=12,
        alignment=1,
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=colors.HexColor("#0a4f44"),
        spaceAfter=6,
    )

    story = []
    story.append(Paragraph("Rapport de Presence", title_style))
    story.append(
        Paragraph(
            f"Periode: {start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
            styles["Normal"],
        )
    )
    if selected_formation_id:
        formation = Formation.query.get(selected_formation_id)
        if formation:
            story.append(Paragraph(f"Formation: {formation.nom_formation}", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Resume", heading_style))
    summary_data = [
        ["Jours ouvrables", "Presences saisies", "Total heures"],
        [
            str(len(workdays)),
            str(len(presences)),
            f"{sum(p.duree_heures for p in presences):.2f} h",
        ],
    ]
    summary_table = Table(summary_data, colWidths=[2 * inch, 2 * inch, 2 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d7a66")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 11),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.2 * inch))

    chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'chart_eleves.png')
    if create_presence_chart(presences_by_eleve, eleves, chart_path):
        story.append(Paragraph("Graphique - Heures par eleve", heading_style))
        story.append(Image(chart_path, width=5*inch, height=2.5*inch))
        story.append(Spacer(1, 0.15 * inch))

    formation_chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'chart_formations.png')
    if create_formation_chart(presences, formation_chart_path):
        story.append(Paragraph("Graphique - Repartition par formation", heading_style))
        story.append(Image(formation_chart_path, width=4*inch, height=3*inch))
        story.append(Spacer(1, 0.15 * inch))

    for row in eleves:
        eleve_name = row.nom_complet
        items = presences_by_eleve.get(row.id, [])
        total_heures = sum(item.duree_heures for item in items)
        jours_presents = len(present_days_by_eleve.get(row.id, set()))
        absences = sorted(workdays_set - present_days_by_eleve.get(row.id, set()))

        story.append(Paragraph(f"Eleve: {eleve_name} ({row.email})", heading_style))
        story.append(
            Paragraph(
                f"Jours presents: {jours_presents} | Jours absents: {len(absences)} | Total heures: {total_heures:.2f} h",
                styles["Normal"],
            )
        )

        if items:
            presences_data = [["Date", "Formation", "Debut", "Fin", "Duree"]]
            for p in items:
                presences_data.append(
                    [
                        p.date.strftime("%d/%m/%Y"),
                        p.formation.nom_formation,
                        p.heure_debut.strftime("%H:%M"),
                        p.heure_fin.strftime("%H:%M"),
                        f"{p.duree_heures:.2f} h",
                    ]
                )
            presences_table = Table(presences_data)
            presences_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d7a66")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
                        ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                    ]
                )
            )
            story.append(presences_table)

        if absences:
            story.append(
                Paragraph(
                    f"Absences (jours ouvrables): {', '.join(d.strftime('%d/%m/%Y') for d in absences)}",
                    styles["Normal"],
                )
            )

        story.append(Spacer(1, 0.15 * inch))
        story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)

    filename = f"rapport_presence_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.pdf"
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/rapport/pdf/details", methods=["GET"])
def rapport_pdf_details():
    start_date = parse_date_arg(request.args.get("start_date")) or date.today().replace(day=1)
    end_date = parse_date_arg(request.args.get("end_date")) or date.today()
    selected_eleve_id = request.args.get("eleve_id", type=int)
    selected_formation_id = request.args.get("formation_id", type=int)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=0.5*inch, leftMargin=0.5*inch, topMargin=0.75*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Rapport Detaille - Presence et Absence", ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0d7a66"), alignment=1)))
    story.append(Paragraph(f"Periode: {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}", styles["Normal"]))
    if selected_formation_id:
        formation = Formation.query.get(selected_formation_id)
        if formation:
            story.append(Paragraph(f"Formation: {formation.nom_formation}", styles["Normal"]))
    story.append(Spacer(1, 0.2*inch))

    eleves = Eleve.query.order_by(Eleve.nom.asc()).all()
    if selected_eleve_id:
        eleves = [e for e in eleves if e.id == selected_eleve_id]

    presences_query = Presence.query.filter(Presence.date >= start_date, Presence.date <= end_date)
    if selected_formation_id:
        presences_query = presences_query.filter(Presence.formation_id == selected_formation_id)
    all_presences = presences_query.all()
    presences_by_eleve = defaultdict(list)
    for p in all_presences:
        presences_by_eleve[p.eleve_id].append(p)

    heading_style_details = ParagraphStyle("CustomHeading", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#0a4f44"), spaceAfter=6)

    chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'chart_details_eleves.png')
    if create_presence_chart(presences_by_eleve, eleves, chart_path):
        story.append(Paragraph("Graphique - Heures par eleve", heading_style_details))
        story.append(Image(chart_path, width=5*inch, height=2.5*inch))
        story.append(Spacer(1, 0.15 * inch))

    formation_chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'chart_details_formations.png')
    if create_formation_chart(all_presences, formation_chart_path):
        story.append(Paragraph("Graphique - Repartition par formation", heading_style_details))
        story.append(Image(formation_chart_path, width=4*inch, height=3*inch))
        story.append(Spacer(1, 0.15 * inch))
        story.append(PageBreak())

    for eleve in eleves:
        presences = presences_by_eleve.get(eleve.id, [])
        
        story.append(Paragraph(f"<b>{eleve.nom_complet}</b> ({eleve.email})", styles["Heading2"]))
        
        if presences:
            data = [["Date", "Jour", "Formation", "Debut", "Fin", "Duree", "Reste"]]
            formation_realised = defaultdict(float)
            
            for p in presences:
                formation_realised[p.formation_id] += p.duree_heures
            
            for p in presences:
                day_name = p.date.strftime("%A")
                remaining = p.formation.total_duration_hours - formation_realised[p.formation_id]
                remaining_str = f"{remaining:.1f}h" if remaining > 0 else "Termine"
                data.append([
                    p.date.strftime("%d/%m/%Y"),
                    day_name,
                    p.formation.nom_formation[:30],
                    p.heure_debut.strftime("%H:%M"),
                    p.heure_fin.strftime("%H:%M"),
                    f"{p.duree_heures:.2f}h",
                    remaining_str
                ])
            
            total_hours = sum(p.duree_heures for p in presences)
            data.append(["", "", "", "", "TOTAL", f"{total_hours:.2f}h", ""])
            
            table = Table(data)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d7a66")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ("BACKGROUND", (-2, -1), (-1, -1), colors.HexColor("#ffffcc")),
                ("FONTNAME", (-2, -1), (-1, -1), "Helvetica-Bold"),
            ]))
            story.append(table)
        
        story.append(Spacer(1, 0.1*inch))
        story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=f"rapport_detaille_{start_date.strftime('%Y%m%d')}.pdf")


@app.route("/rapport/pdf/semaine", methods=["GET"])
def rapport_pdf_semaine():
    start_date = parse_date_arg(request.args.get("start_date")) or date.today()
    start_date = start_date - timedelta(days=start_date.weekday())
    end_date = start_date + timedelta(days=6)
    selected_eleve_id = request.args.get("eleve_id", type=int)
    selected_formation_id = request.args.get("formation_id", type=int)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=0.5*inch, leftMargin=0.5*inch, topMargin=0.75*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Rapport Hebdomadaire", ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0d7a66"), alignment=1)))
    story.append(Paragraph(f"Semaine du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}", styles["Normal"]))
    if selected_formation_id:
        formation = Formation.query.get(selected_formation_id)
        if formation:
            story.append(Paragraph(f"Formation: {formation.nom_formation}", styles["Normal"]))
    story.append(Spacer(1, 0.2*inch))

    eleves = Eleve.query.order_by(Eleve.nom.asc()).all()
    if selected_eleve_id:
        eleves = [e for e in eleves if e.id == selected_eleve_id]

    presences = Presence.query.filter(
        Presence.date >= start_date,
        Presence.date <= end_date,
    )
    if selected_eleve_id:
        presences = presences.filter(Presence.eleve_id == selected_eleve_id)
    if selected_formation_id:
        presences = presences.filter(Presence.formation_id == selected_formation_id)
    presences = presences.join(Eleve).join(Formation).order_by(Presence.date.asc(), Presence.heure_debut.asc()).all()

    presences_by_eleve_semaine = defaultdict(list)
    for p in presences:
        presences_by_eleve_semaine[p.eleve_id].append(p)

    heading_style_semaine = ParagraphStyle("CustomHeading", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#0a4f44"), spaceAfter=6)

    chart_path_semaine = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'chart_semaine_eleves.png')
    if create_presence_chart(presences_by_eleve_semaine, eleves, chart_path_semaine):
        story.append(Paragraph("Graphique - Heures par eleve", heading_style_semaine))
        story.append(Image(chart_path_semaine, width=5*inch, height=2.5*inch))
        story.append(Spacer(1, 0.15 * inch))

    formation_chart_path_semaine = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'chart_semaine_formations.png')
    if create_formation_chart(presences, formation_chart_path_semaine):
        story.append(Paragraph("Graphique - Repartition par formation", heading_style_semaine))
        story.append(Image(formation_chart_path_semaine, width=4*inch, height=3*inch))
        story.append(Spacer(1, 0.15 * inch))

    presences_by_eleve_and_date = defaultdict(lambda: defaultdict(list))
    for presence in presences:
        presences_by_eleve_and_date[presence.eleve_id][presence.date].append(presence)

    data = [["Eleve", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Total Heures"]]
    
    for eleve in eleves:
        row = [eleve.nom_complet]
        total = 0
        for day_offset in range(5):
            check_date = start_date + timedelta(days=day_offset)
            day_presences = presences_by_eleve_and_date.get(eleve.id, {}).get(check_date, [])
            if day_presences:
                day_total = sum(p.duree_heures for p in day_presences)
                formations = ", ".join(sorted({p.formation.nom_formation for p in day_presences}))
                row.append(f"{day_total:.2f}h\n{formations}")
                total += day_total
            else:
                row.append("Absent")
        row.append(f"{total:.2f}h")
        data.append(row)

    table = Table(data)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d7a66")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 1, colors.grey),
    ]))
    story.append(table)

    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=f"rapport_semaine_{start_date.strftime('%Y%m%d')}.pdf")


@app.route("/rapport/pdf/mois", methods=["GET"])
def rapport_pdf_mois():
    year = request.args.get("year", type=int, default=date.today().year)
    month = request.args.get("month", type=int, default=date.today().month)
    selected_eleve_id = request.args.get("eleve_id", type=int)
    selected_formation_id = request.args.get("formation_id", type=int)

    from calendar import monthrange
    last_day = monthrange(year, month)[1]
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=0.5*inch, leftMargin=0.5*inch, topMargin=0.75*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Rapport Mensuel", ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0d7a66"), alignment=1)))
    story.append(Paragraph(f"{start_date.strftime('%B %Y')}", styles["Normal"]))
    if selected_formation_id:
        formation = Formation.query.get(selected_formation_id)
        if formation:
            story.append(Paragraph(f"Formation: {formation.nom_formation}", styles["Normal"]))
    story.append(Spacer(1, 0.2*inch))

    eleves = Eleve.query.order_by(Eleve.nom.asc()).all()
    if selected_eleve_id:
        eleves = [e for e in eleves if e.id == selected_eleve_id]

    presences_query = Presence.query.filter(Presence.date >= start_date, Presence.date <= end_date)
    if selected_formation_id:
        presences_query = presences_query.filter(Presence.formation_id == selected_formation_id)
    all_presences = presences_query.all()
    presences_by_eleve = defaultdict(list)
    for p in all_presences:
        presences_by_eleve[p.eleve_id].append(p)

    heading_style_mois = ParagraphStyle("CustomHeading", parent=styles["Heading2"], fontSize=12, textColor=colors.HexColor("#0a4f44"), spaceAfter=6)

    chart_path_mois = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'chart_mois_eleves.png')
    if create_presence_chart(presences_by_eleve, eleves, chart_path_mois):
        story.append(Paragraph("Graphique - Heures par eleve", heading_style_mois))
        story.append(Image(chart_path_mois, width=5*inch, height=2.5*inch))
        story.append(Spacer(1, 0.15 * inch))

    formation_chart_path_mois = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'chart_mois_formations.png')
    if create_formation_chart(all_presences, formation_chart_path_mois):
        story.append(Paragraph("Graphique - Repartition par formation", heading_style_mois))
        story.append(Image(formation_chart_path_mois, width=4*inch, height=3*inch))
        story.append(Spacer(1, 0.15 * inch))
        story.append(PageBreak())

    for eleve in eleves:
        presences = presences_by_eleve.get(eleve.id, [])
        
        total_hours = sum(p.duree_heures for p in presences)
        
        story.append(Paragraph(f"<b>{eleve.nom_complet}</b>", styles["Heading2"]))
        story.append(Paragraph(f"Total heures: {total_hours:.2f}h | Nombre de sequences: {len(presences)}", styles["Normal"]))
        
        if presences:
            data = [["Date", "Jour", "Formation", "Duree"]]
            for p in presences:
                data.append([
                    p.date.strftime("%d/%m"),
                    p.date.strftime("%a"),
                    p.formation.nom_formation,
                    f"{p.duree_heures:.2f}h"
                ])
            
            table = Table(data)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d7a66")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
            ]))
            story.append(table)
        
        story.append(Spacer(1, 0.15*inch))
        story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)
    return send_file(buffer, mimetype="application/pdf", as_attachment=True, download_name=f"rapport_mois_{year}{month:02d}.pdf")


@app.route("/presence/delete/<int:id>", methods=["POST"])
def presence_delete(id):
    item = Presence.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash("Presence supprimee avec succes.", "success")
    return redirect(url_for("presence"))


@app.route("/rapport/pdf/complet", methods=["GET"])
def rapport_pdf_complet():
    today = date.today()
    first_of_month = today.replace(day=1)

    start_date = parse_date_arg(request.args.get("start_date")) or first_of_month
    end_date = parse_date_arg(request.args.get("end_date")) or today
    selected_eleve_id = request.args.get("eleve_id", type=int)
    selected_formation_id = request.args.get("formation_id", type=int)

    formation = None
    if selected_formation_id:
        formation = Formation.query.get(selected_formation_id)
        formations = [formation] if formation else []
    else:
        formations = Formation.query.order_by(Formation.nom_formation.asc()).all()

    eleves_query = Eleve.query.order_by(Eleve.nom.asc(), Eleve.prenom.asc())
    if selected_eleve_id:
        eleves_query = eleves_query.filter(Eleve.id == selected_eleve_id)
    elif selected_formation_id and formation:
        eleves_query = eleves_query.join(eleve_formations).filter(eleve_formations.c.formation_id == selected_formation_id)
    eleves = eleves_query.all()

    presences_query = Presence.query.filter(
        Presence.date >= start_date,
        Presence.date <= end_date,
    )
    if selected_eleve_id:
        presences_query = presences_query.filter(Presence.eleve_id == selected_eleve_id)
    if selected_formation_id:
        presences_query = presences_query.filter(Presence.formation_id == selected_formation_id)

    presences = (
        presences_query.join(Eleve)
        .join(Formation)
        .order_by(Presence.date.asc(), Presence.heure_debut.asc())
        .all()
    )

    workdays = list(iter_weekdays(start_date, end_date))
    workdays_set = set(workdays)

    presences_by_eleve = defaultdict(list)
    present_days_by_eleve = defaultdict(set)
    for p in presences:
        presences_by_eleve[p.eleve_id].append(p)
        present_days_by_eleve[p.eleve_id].add(p.date)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.5 * inch,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=20,
        textColor=colors.HexColor("#0d7a66"),
        spaceAfter=12,
        alignment=1,
    )
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=14,
        textColor=colors.HexColor("#0a4f44"),
        spaceAfter=8,
    )
    subheading_style = ParagraphStyle(
        "CustomSubHeading",
        parent=styles["Heading3"],
        fontSize=11,
        textColor=colors.HexColor("#0a4f44"),
        spaceAfter=4,
    )

    story = []
    if selected_formation_id and formation:
        story.append(Paragraph(f"RAPPORT: {formation.nom_formation}", title_style))
    else:
        story.append(Paragraph("RAPPORT DE FORMATION", title_style))
    story.append(
        Paragraph(
            f"<b>Periode:</b> {start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}",
            styles["Normal"],
        )
    )
    if selected_formation_id and formation:
        story.append(Paragraph(f"<b>Formation:</b> {formation.nom_formation}", styles["Normal"]))
    story.append(Spacer(1, 0.3 * inch))

    total_heures_global = sum(p.duree_heures for p in presences)
    story.append(Paragraph("RESUME GLOBAL", heading_style))
    
    summary_data = [
        ["", ""],
        ["Nombre d'eleves", str(len(eleves))],
        ["Nombre de presences", str(len(presences))],
        ["Total heures de formation", f"{total_heures_global:.1f} h"],
        ["Jours ouvrables", str(len(workdays))],
    ]
    summary_table = Table(summary_data, colWidths=[3 * inch, 2 * inch])
    summary_table.setStyle(
        TableStyle([
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 11),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#0a4f44")),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.grey),
        ])
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.3 * inch))

    story.append(Paragraph("STATISTIQUES PAR FORMATION", heading_style))
    
    for formation in formations:
        formation_presences = [p for p in presences if p.formation_id == formation.id]
        if not formation_presences and selected_formation_id and formation.id != selected_formation_id:
            continue
        
        realised = sum(p.duree_heures for p in formation_presences)
        remaining = max(formation.total_duration_hours - realised, 0)
        progress = (realised / formation.total_duration_hours * 100) if formation.total_duration_hours > 0 else 0
        
        story.append(Paragraph(f"{formation.nom_formation}", subheading_style))
        formation_data = [
            ["Total formation", "Realise", "Reste", "Progression"],
            [
                f"{formation.total_duration_hours} h",
                f"{realised:.1f} h",
                f"{remaining:.1f} h",
                f"{progress:.1f}%",
            ],
        ]
        formation_table = Table(formation_data, colWidths=[1.5 * inch, 1.5 * inch, 1.5 * inch, 1.5 * inch])
        formation_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d7a66")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("GRID", (0, 0), (-1, -1), 1, colors.grey),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
            ])
        )
        story.append(formation_table)
        story.append(Spacer(1, 0.15 * inch))

    if eleves:
        chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'complet_eleves.png')
        if create_presence_chart(presences_by_eleve, eleves, chart_path):
            story.append(Paragraph("Graphique - Heures par eleve", heading_style))
            story.append(Image(chart_path, width=6 * inch, height = 3 * inch))
            story.append(Spacer(1, 0.2 * inch))

    if presences:
        formation_chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'complet_formations.png')
        if create_formation_chart(presences, formation_chart_path):
            story.append(Paragraph("Graphique - Repartition par formation", heading_style))
            story.append(Image(formation_chart_path, width=4 * inch, height = 3 * inch))
            story.append(Spacer(1, 0.2 * inch))

    story.append(PageBreak())
    story.append(Paragraph("DETAIL PAR ELEVE", heading_style))
    story.append(Spacer(1, 0.1 * inch))

    for eleve in eleves:
        items = presences_by_eleve.get(eleve.id, [])
        total_heures = round(sum(item.duree_heures for item in items), 2)
        jours_presents = len(present_days_by_eleve.get(eleve.id, set()))
        absences = sorted(workdays_set - present_days_by_eleve.get(eleve.id, set()))
        
        formation_hours = defaultdict(float)
        for p in items:
            formation_hours[p.formation_id] += p.duree_heures
        
        story.append(Paragraph(f"<b>{eleve.nom_complet}</b>", subheading_style))
        story.append(Paragraph(f"Email: {eleve.email} | Tel: {eleve.numero or 'N/A'}", styles["Normal"]))
        story.append(Paragraph(f"<b>Total heures:</b> {total_heures} h | <b>Jours presents:</b> {jours_presents} | <b>Jours absents:</b> {len(absences)}", styles["Normal"]))
        
        if items:
            story.append(Paragraph("Presences:", subheading_style))
            data = [["Date", "Formation", "Debut", "Fin", "Duree"]]
            for p in items:
                data.append([
                    p.date.strftime("%d/%m/%Y"),
                    p.formation.nom_formation[:25],
                    p.heure_debut.strftime("%H:%M"),
                    p.heure_fin.strftime("%H:%M"),
                    f"{p.duree_heures:.2f} h",
                ])
            
            table = Table(data, colWidths=[1.2 * inch, 2 * inch, 0.7 * inch, 0.7 * inch, 0.8 * inch])
            table.setStyle(
                TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0d7a66")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ])
            )
            story.append(table)
        
        if absences:
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph("Jours d'absence (jours ouvrables):", styles["Normal"]))
            absence_dates = ", ".join([d.strftime('%d/%m/%Y') for d in absences[:10]])
            if len(absences) > 10:
                absence_dates += f" ... (+{len(absences) - 10} autres)"
            story.append(Paragraph(absence_dates, styles["Normal"]))
        
        story.append(Spacer(1, 0.2 * inch))
        story.append(PageBreak())

    doc.build(story)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"rapport_complet_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.pdf",
    )


@app.route("/salaire", methods=["GET", "POST"])
def salaire():
    salary_setting = Salary.query.first()
    
    if request.method == "POST":
        rate = request.form.get("rate_per_hour", type=float, default=15.0)
        if salary_setting:
            salary_setting.rate_per_hour = rate
        else:
            salary_setting = Salary(rate_per_hour=rate)
            db.session.add(salary_setting)
        db.session.commit()
        flash("Taux mis a jour avec succes.", "success")
        return redirect(url_for("salaire"))
    
    rate = salary_setting.rate_per_hour if salary_setting else 15.0
    
    start_date = parse_date_arg(request.args.get("start_date"))
    end_date = parse_date_arg(request.args.get("end_date"))
    
    formations = Formation.query.all()
    formation_stats = []
    
    for formation in formations:
        query = Presence.query.filter_by(formation_id=formation.id)
        if start_date:
            query = query.filter(Presence.date >= start_date)
        if end_date:
            query = query.filter(Presence.date <= end_date)
        presences = query.all()
        
        unique_slots = {}
        for p in presences:
            slot = (p.date, p.heure_debut, p.heure_fin)
            if slot not in unique_slots:
                unique_slots[slot] = p.duree_heures
        
        total_hours = sum(unique_slots.values())
        total_salary = total_hours * rate
        
        formation_stats.append({
            "formation": formation,
            "total_hours": total_hours,
            "total_salary": total_salary,
            "sessions": len(unique_slots)
        })
    
    total_heures_all = sum(f["total_hours"] for f in formation_stats)
    total_salary_all = sum(f["total_salary"] for f in formation_stats)
    
    month_hours = defaultdict(float)
    month_salary = defaultdict(float)
    
    for formation in formations:
        query = Presence.query.filter_by(formation_id=formation.id)
        if start_date:
            query = query.filter(Presence.date >= start_date)
        if end_date:
            query = query.filter(Presence.date <= end_date)
        presences = query.all()
        
        seen_slots = set()
        for p in presences:
            slot = (p.date, p.heure_debut, p.heure_fin)
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            month_key = p.date.strftime("%Y-%m")
            month_hours[month_key] += p.duree_heures
    
    for m in month_hours:
        month_salary[m] = month_hours[m] * rate
    
    sorted_months = sorted(month_hours.keys())
    month_data = [{"month": datetime.strptime(m, "%Y-%m").strftime("%B %Y"), "hours": month_hours[m], "salary": month_salary[m]} for m in sorted_months]
    
    chart_path = None
    if month_hours:
        chart_path = os.path.join(app.config.get('TEMP_FOLDER', '/tmp'), 'salary_monthly.png')
        fig, ax = plt.subplots(figsize=(10, 5))
        months = [datetime.strptime(m, "%Y-%m").strftime("%b") for m in sorted_months]
        salaries = [month_salary[m] for m in sorted_months]
        ax.bar(months, salaries, color='#28a745')
        ax.set_xlabel('Mois')
        ax.set_ylabel('Salaire (DT)')
        ax.set_title('Salaire par mois')
        for i, v in enumerate(salaries):
            ax.text(i, v + 5, f'{v:.0f} DT', ha='center', fontsize=9)
        plt.tight_layout()
        plt.savefig(chart_path, dpi=100)
        plt.close()
    
    week_hours = defaultdict(float)
    week_salary = defaultdict(float)
    
    for formation in formations:
        query = Presence.query.filter_by(formation_id=formation.id)
        if start_date:
            query = query.filter(Presence.date >= start_date)
        if end_date:
            query = query.filter(Presence.date <= end_date)
        presences = query.all()
        
        seen_slots = set()
        for p in presences:
            slot = (p.date, p.heure_debut, p.heure_fin)
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            iso = p.date.isocalendar()
            week_key = f"{iso[0]}-S{iso[1]:02d}"
            week_hours[week_key] += p.duree_heures
    
    for w in week_hours:
        week_salary[w] = week_hours[w] * rate
    
    sorted_weeks = sorted(week_hours.keys())
    week_data = [{"week": w, "hours": week_hours[w], "salary": week_salary[w]} for w in sorted_weeks]
    
    all_sessions = []
    seen_slots = set()
    for formation in formations:
        query = Presence.query.filter_by(formation_id=formation.id)
        if start_date:
            query = query.filter(Presence.date >= start_date)
        if end_date:
            query = query.filter(Presence.date <= end_date)
        for p in query.all():
            slot = (p.date, p.heure_debut, p.heure_fin)
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            all_sessions.append({
                "id": p.id,
                "formation": formation.nom_formation,
                "date": p.date,
                "heure_debut": p.heure_debut,
                "heure_fin": p.heure_fin,
                "duree": p.duree_heures,
                "salary": p.duree_heures * rate
            })
    
    all_sessions = sorted(all_sessions, key=lambda x: x["date"], reverse=True)
    
    return render_template(
        "salaire.html",
        rate=rate,
        formation_stats=formation_stats,
        total_heures_all=total_heures_all,
        total_salary_all=total_salary_all,
        month_data=month_data,
        week_data=week_data,
        chart_path=chart_path,
        start_date=start_date,
        end_date=end_date,
        all_sessions=all_sessions,
    )


@app.route("/salaire/pdf")
def salaire_pdf():
    salary_setting = Salary.query.first()
    rate = salary_setting.rate_per_hour if salary_setting else 15.0
    
    start_date = parse_date_arg(request.args.get("start_date"))
    end_date = parse_date_arg(request.args.get("end_date"))
    
    formations = Formation.query.all()
    
    all_sessions = []
    seen_slots = set()
    total_hours = 0.0
    
    for formation in formations:
        query = Presence.query.filter_by(formation_id=formation.id)
        if start_date:
            query = query.filter(Presence.date >= start_date)
        if end_date:
            query = query.filter(Presence.date <= end_date)
        
        formation_hours = 0.0
        for p in query.all():
            slot = (p.date, p.heure_debut, p.heure_fin)
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            all_sessions.append({
                "formation": formation.nom_formation,
                "date": p.date,
                "heure_debut": p.heure_debut.strftime("%H:%M"),
                "heure_fin": p.heure_fin.strftime("%H:%M"),
                "duree": p.duree_heures,
                "salary": p.duree_heures * rate
            })
            formation_hours += p.duree_heures
            total_hours += p.duree_heures
    
    all_sessions = sorted(all_sessions, key=lambda x: x["date"], reverse=True)
    total_salary = total_hours * rate
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=30)
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=18, spaceAfter=20, alignment=1)
    heading_style = ParagraphStyle("Heading", parent=styles["Heading2"], fontSize=14, spaceAfter=10, textColor=colors.HexColor("#0a4f44"))
    subheading_style = ParagraphStyle("SubHeading", parent=styles["Heading3"], fontSize=11, textColor=colors.HexColor("#0a4f44"), spaceAfter=5)
    
    story = []
    story.append(Paragraph("RAPPORT DE SALAIRE", title_style))
    
    if start_date and end_date:
        story.append(Paragraph(f"<b>Periode:</b> {start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}", styles["Normal"]))
    story.append(Paragraph(f"<b>Taux horaire:</b> {rate} DT/heure", styles["Normal"]))
    story.append(Spacer(1, 0.2 * inch))
    
    story.append(Paragraph("RESUME", heading_style))
    summary_data = [
        ["Total heures", f"{total_hours:.1f} h"],
        ["Total salaire", f"{total_salary:.2f} DT"],
        ["Nombre de seances", str(len(all_sessions))],
    ]
    summary_table = Table(summary_data, colWidths=[2.5 * inch, 2 * inch])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#0a4f44")),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.3 * inch))
    
    if start_date and end_date:
        month_hours = defaultdict(float)
        month_salary = defaultdict(float)
        for session in all_sessions:
            month_key = datetime.strptime(session["date"].strftime("%Y-%m"), "%Y-%m").strftime("%B %Y")
            month_hours[month_key] += session["duree"]
        for m, h in month_hours.items():
            month_salary[m] = h * rate
        
        if month_hours:
            story.append(Paragraph("PAR MOIS", heading_style))
            month_data = [["Mois", "Heures", "Salaire"]]
            for m in sorted(month_hours.keys(), key=lambda x: datetime.strptime(x, "%B %Y")):
                month_name = datetime.strptime(m, "%B %Y").strftime("%B %Y")
                month_data.append([month_name, f"{month_hours[m]:.1f} h", f"{month_salary[m]:.2f} DT"])
            month_table = Table(month_data, colWidths=[2.5 * inch, 1.5 * inch, 1.5 * inch])
            month_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f5e9")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("ALIGN", (1, 0), (-1, -1), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]))
            story.append(month_table)
            story.append(Spacer(1, 0.3 * inch))
    
    story.append(Paragraph("DETAIL DES SEANCES", heading_style))
    
    session_data = [["Formation", "Date", "Heure", "Duree", "Salaire"]]
    formation_colors = {
        "dev web": colors.HexColor("#e3f2fd"),
        "Python": colors.HexColor("#e8f5e9"),
    }
    for i, s in enumerate(all_sessions):
        formation = s["formation"]
        bg_color = formation_colors.get(formation, colors.white)
        formation_cell = Paragraph(f"<font color='#0a4f44'><b>{formation}</b></font>", styles["Normal"])
        session_data.append([
            formation_cell,
            s["date"].strftime("%d/%m/%Y"),
            f"{s['heure_debut']}-{s['heure_fin']}",
            f"{s['duree']:.2f} h",
            f"{s['salary']:.2f} DT"
        ])
    
    session_table = Table(session_data, colWidths=[1.3 * inch, 1 * inch, 1 * inch, 0.8 * inch, 1 * inch])
    
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0a4f44")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    
    for i in range(1, len(all_sessions) + 1):
        formation = all_sessions[i - 1]["formation"]
        bg_color = formation_colors.get(formation, colors.white)
        style.append(("BACKGROUND", (0, i), (0, i), bg_color))
    
    session_table.setStyle(TableStyle(style))
    story.append(session_table)
    
    doc.build(story)
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"rapport_salaire_{date.today().strftime('%Y%m%d')}.pdf",
    )


if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False), port=int(os.environ.get("PORT", "5001")))
