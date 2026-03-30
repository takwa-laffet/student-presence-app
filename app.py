import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

import pymysql
from flask import Flask, flash, redirect, render_template, request, url_for, send_file, jsonify
from flask_wtf.csrf import CSRFProtect
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from config import Config
from forms import EleveForm, FormationForm, PresenceForm
from models import Eleve, Formation, Presence, db


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
    connection = pymysql.connect(
        host=app.config["DB_HOST"],
        port=int(app.config["DB_PORT"]),
        user=app.config["DB_USER"],
        password=app.config["DB_PASSWORD"],
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{app.config['DB_NAME']}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()


def ensure_schema_compatibility():
    connection = pymysql.connect(
        host=app.config["DB_HOST"],
        port=int(app.config["DB_PORT"]),
        user=app.config["DB_USER"],
        password=app.config["DB_PASSWORD"],
        database=app.config["DB_NAME"],
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'eleves'
                  AND COLUMN_NAME = 'numero'
                """,
                (app.config["DB_NAME"],),
            )
            has_numero = cursor.fetchone()[0] > 0
            if not has_numero:
                cursor.execute("ALTER TABLE eleves ADD COLUMN numero VARCHAR(30) NULL AFTER email")

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'eleves'
                  AND COLUMN_NAME = 'formation_id'
                """,
                (app.config["DB_NAME"],),
            )
            has_formation_id = cursor.fetchone()[0] > 0
            if not has_formation_id:
                cursor.execute("ALTER TABLE eleves ADD COLUMN formation_id INT NULL AFTER numero")

            cursor.execute(
                """
                UPDATE eleves e
                SET e.formation_id = (
                    SELECT p.formation_id
                    FROM presences p
                    WHERE p.eleve_id = e.id
                    ORDER BY p.date DESC, p.heure_fin DESC, p.id DESC
                    LIMIT 1
                )
                WHERE e.formation_id IS NULL
                """
            )

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'eleves'
                  AND INDEX_NAME = 'idx_eleves_formation_id'
                """,
                (app.config["DB_NAME"],),
            )
            has_formation_idx = cursor.fetchone()[0] > 0
            if not has_formation_idx:
                cursor.execute("CREATE INDEX idx_eleves_formation_id ON eleves(formation_id)")

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.TABLE_CONSTRAINTS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'eleves'
                  AND CONSTRAINT_NAME = 'fk_eleves_formation'
                  AND CONSTRAINT_TYPE = 'FOREIGN KEY'
                """,
                (app.config["DB_NAME"],),
            )
            has_formation_fk = cursor.fetchone()[0] > 0
            if not has_formation_fk:
                cursor.execute(
                    """
                    ALTER TABLE eleves
                    ADD CONSTRAINT fk_eleves_formation
                    FOREIGN KEY (formation_id) REFERENCES formations(id)
                    ON UPDATE CASCADE
                    ON DELETE SET NULL
                    """
                )

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'formations'
                  AND COLUMN_NAME = 'total_duration_hours'
                """,
                (app.config["DB_NAME"],),
            )
            has_total_duration_hours = cursor.fetchone()[0] > 0
            if not has_total_duration_hours:
                cursor.execute(
                    """
                    ALTER TABLE formations
                    ADD COLUMN total_duration_hours INT NOT NULL DEFAULT 40 AFTER description
                    """
                )

            cursor.execute(
                """
                UPDATE formations
                SET total_duration_hours = 40
                WHERE total_duration_hours IS NULL
                   OR total_duration_hours < 1
                """
            )

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME = 'formations'
                  AND COLUMN_NAME = 'session_duration_hours'
                """,
                (app.config["DB_NAME"],),
            )
            has_session_duration = cursor.fetchone()[0] > 0
            if not has_session_duration:
                cursor.execute(
                    """
                    ALTER TABLE formations
                    ADD COLUMN session_duration_hours INT NOT NULL DEFAULT 2 AFTER total_duration_hours
                    """
                )
    finally:
        connection.close()


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


@app.route("/eleves", methods=["GET", "POST"])
def eleves():
    form = EleveForm()
    formations = Formation.query.order_by(Formation.nom_formation.asc()).all()
    form.formation_id.choices = [(0, "Aucune")] + [(f.id, f.nom_formation) for f in formations]

    if form.validate_on_submit():
        eleve = Eleve(
            nom=form.nom.data.strip(),
            prenom=form.prenom.data.strip(),
            email=form.email.data.strip().lower(),
            numero=(form.numero.data or "").strip() or None,
            formation_id=form.formation_id.data if form.formation_id.data and form.formation_id.data > 0 else None,
        )
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
    form.formation_id.choices = [(0, "Aucune")] + [(f.id, f.nom_formation) for f in formations]
    
    current_formation = eleve.formation
    if request.method == "GET":
        form.formation_id.data = eleve.formation_id or 0

    if form.validate_on_submit():
        eleve.nom = form.nom.data.strip()
        eleve.prenom = form.prenom.data.strip()
        eleve.email = form.email.data.strip().lower()
        eleve.numero = (form.numero.data or "").strip() or None
        eleve.formation_id = form.formation_id.data if form.formation_id.data and form.formation_id.data > 0 else None

        try:
            db.session.commit()
            flash("Eleve modifie avec succes.", "success")
            return redirect(url_for("eleves"))
        except IntegrityError:
            db.session.rollback()
            flash("Cet email existe deja.", "danger")

    return render_template("eleve_form.html", form=form, eleve=eleve, current_formation=current_formation)


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
        if eleve and eleve.formation_id and eleve.formation_id != form.formation_id.data:
            flash(
                "Cet eleve est affecte a une autre formation.",
                "danger",
            )
            return redirect(url_for("presence"))

        if eleve and not eleve.formation_id:
            eleve.formation_id = form.formation_id.data

        exists = Presence.query.filter_by(
            eleve_id=form.eleve_id.data,
            date=form.date.data,
        ).first()

        if exists:
            flash(
                "Doublon detecte: cet eleve a deja une presence pour cette date.",
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

    assigned_formation_by_eleve = {}
    last_slot_by_eleve = {}
    history = Presence.query.order_by(Presence.date.desc(), Presence.heure_debut.desc()).all()
    for e in eleves:
        if e.formation_id:
            assigned_formation_by_eleve[e.id] = e.formation_id
    for item in history:
        if item.eleve_id not in assigned_formation_by_eleve:
            assigned_formation_by_eleve[item.eleve_id] = item.formation_id
        if item.eleve_id not in last_slot_by_eleve:
            last_slot_by_eleve[item.eleve_id] = {
                "heure_debut": item.heure_debut.strftime("%H:%M"),
                "heure_fin": item.heure_fin.strftime("%H:%M"),
            }

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
        assigned_formation_by_eleve=assigned_formation_by_eleve,
        last_slot_by_eleve=last_slot_by_eleve,
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

        existing_other_formation = Presence.query.filter(
            Presence.eleve_id == form.eleve_id.data,
            Presence.formation_id != form.formation_id.data,
            Presence.id != item.id,
        ).first()

        if existing_other_formation:
            flash(
                "Cet eleve est deja affecte a une autre formation et ne peut pas etre ajoute ici.",
                "danger",
            )
            return redirect(url_for("presence_edit", id=item.id))

        exists = Presence.query.filter(
            Presence.eleve_id == form.eleve_id.data,
            Presence.date == form.date.data,
            Presence.id != item.id,
        ).first()

        if exists:
            flash(
                "Doublon detecte: cet eleve a deja une presence pour cette date.",
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
        Eleve.query.filter(Eleve.formation_id == formation_id)
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

        if eleve.formation_id and eleve.formation_id != formation_id:
            skipped += 1
            continue

        if not eleve.formation_id:
            eleve.formation_id = formation_id
            
        try:
            existing = Presence.query.filter_by(
                eleve_id=eleve_id,
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
        
        # Calculate realised hours per formation for this student
        formation_realised_hours = defaultdict(float)
        for p in items:
            formation_realised_hours[p.formation_id] += p.duree_heures

        rows.append(
            {
                "eleve": e,
                "items": items,
                "total_heures": total_heures,
                "jours_presents": jours_presents,
                "jours_absents": len(absences),
                "dates_absence": absences,
                "formation_realised_hours": dict(formation_realised_hours),
            }
        )

    total_presence_records = len(presences)
    total_heures_global = round(sum(p.duree_heures for p in presences), 2)

    return render_template(
        "rapport.html",
        rows=rows,
        eleves_all=Eleve.query.order_by(Eleve.nom.asc(), Eleve.prenom.asc()).all(),
        selected_eleve_id=selected_eleve_id,
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

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=0.5*inch, leftMargin=0.5*inch, topMargin=0.75*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Rapport Detaille - Presence et Absence", ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0d7a66"), alignment=1)))
    story.append(Paragraph(f"Periode: {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}", styles["Normal"]))
    story.append(Spacer(1, 0.2*inch))

    eleves = Eleve.query.order_by(Eleve.nom.asc()).all()
    if selected_eleve_id:
        eleves = [e for e in eleves if e.id == selected_eleve_id]

    for eleve in eleves:
        presences = Presence.query.filter(Presence.eleve_id == eleve.id, Presence.date >= start_date, Presence.date <= end_date).order_by(Presence.date.asc()).all()
        
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

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=0.5*inch, leftMargin=0.5*inch, topMargin=0.75*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("Rapport Hebdomadaire", ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, textColor=colors.HexColor("#0d7a66"), alignment=1)))
    story.append(Paragraph(f"Semaine du {start_date.strftime('%d/%m/%Y')} au {end_date.strftime('%d/%m/%Y')}", styles["Normal"]))
    story.append(Spacer(1, 0.2*inch))

    eleves = Eleve.query.order_by(Eleve.nom.asc()).all()
    if selected_eleve_id:
        eleves = [e for e in eleves if e.id == selected_eleve_id]

    data = [["Eleve", "Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Total Heures"]]
    
    for eleve in eleves:
        row = [eleve.nom_complet]
        total = 0
        for day_offset in range(5):
            check_date = start_date + timedelta(days=day_offset)
            p = Presence.query.filter_by(eleve_id=eleve.id, date=check_date).first()
            if p:
                row.append(f"{p.duree_heures:.2f}h")
                total += p.duree_heures
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
    story.append(Spacer(1, 0.2*inch))

    eleves = Eleve.query.order_by(Eleve.nom.asc()).all()
    if selected_eleve_id:
        eleves = [e for e in eleves if e.id == selected_eleve_id]

    for eleve in eleves:
        presences = Presence.query.filter(Presence.eleve_id == eleve.id, Presence.date >= start_date, Presence.date <= end_date).order_by(Presence.date.asc()).all()
        
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


if __name__ == "__main__":
    app.run(debug=app.config.get("DEBUG", False), port=int(os.environ.get("PORT", "5001")))
