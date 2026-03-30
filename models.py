from datetime import datetime

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


class Eleve(db.Model):
    __tablename__ = "eleves"

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(120), nullable=False)
    prenom = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    numero = db.Column(db.String(30), nullable=True)
    formation_id = db.Column(db.Integer, db.ForeignKey("formations.id"), nullable=True)

    presences = db.relationship(
        "Presence", back_populates="eleve", cascade="all, delete-orphan"
    )
    formation = db.relationship("Formation", back_populates="eleves")

    @property
    def nom_complet(self):
        return f"{self.prenom} {self.nom}"


class Formation(db.Model):
    __tablename__ = "formations"

    id = db.Column(db.Integer, primary_key=True)
    nom_formation = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    total_duration_hours = db.Column(db.Integer, nullable=False, default=0)
    session_duration_hours = db.Column(db.Integer, nullable=False, default=2)

    presences = db.relationship(
        "Presence", back_populates="formation", cascade="all, delete-orphan"
    )
    eleves = db.relationship("Eleve", back_populates="formation")

    @property
    def realised_duration_hours(self):
        seen_slots = set()
        total = 0.0

        for presence in self.presences:
            slot = (presence.date, presence.heure_debut, presence.heure_fin)
            if slot in seen_slots:
                continue
            seen_slots.add(slot)
            total += presence.duree_heures

        return round(total, 2)

    @property
    def remaining_duration_hours(self):
        return round(max(float(self.total_duration_hours or 0) - self.realised_duration_hours, 0), 2)


class Presence(db.Model):
    __tablename__ = "presences"
    __table_args__ = (
        db.UniqueConstraint("eleve_id", "date", name="uq_presence_eleve_date"),
    )

    id = db.Column(db.Integer, primary_key=True)
    eleve_id = db.Column(db.Integer, db.ForeignKey("eleves.id"), nullable=False)
    formation_id = db.Column(db.Integer, db.ForeignKey("formations.id"), nullable=False)
    date = db.Column(db.Date, nullable=False, default=datetime.utcnow)
    heure_debut = db.Column(db.Time, nullable=False)
    heure_fin = db.Column(db.Time, nullable=False)

    eleve = db.relationship("Eleve", back_populates="presences")
    formation = db.relationship("Formation", back_populates="presences")

    @property
    def duree_heures(self):
        debut = datetime.combine(self.date, self.heure_debut)
        fin = datetime.combine(self.date, self.heure_fin)
        if fin <= debut:
            return 0.0
        delta = fin - debut
        return round(delta.total_seconds() / 3600, 2)
