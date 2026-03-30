from flask_wtf import FlaskForm
from wtforms import DateField, IntegerField, SelectField, StringField, SubmitField, TextAreaField, TimeField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional


class EleveForm(FlaskForm):
    nom = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    prenom = StringField("Prenom", validators=[DataRequired(), Length(max=120)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=200)])
    numero = StringField("Numero", validators=[Optional(), Length(max=30)])
    formation_id = SelectField("Formation (optionnel)", coerce=int, validators=[Optional()], choices=[])
    submit = SubmitField("Enregistrer")


class FormationForm(FlaskForm):
    nom_formation = StringField(
        "Nom de la formation", validators=[DataRequired(), Length(max=200)]
    )
    description = TextAreaField("Description")
    total_duration_hours = IntegerField(
        "Duree totale de formation (heures)",
        validators=[DataRequired(), NumberRange(min=1, max=5000)],
        default=40,
    )
    session_duration_hours = SelectField(
        "Duree de la seance (heures)",
        choices=[(2, "2 heures"), (4, "4 heures")],
        default=2,
    )
    submit = SubmitField("Enregistrer")


class PresenceForm(FlaskForm):
    eleve_id = SelectField("Eleve", coerce=int, validators=[DataRequired()])
    formation_id = SelectField("Formation", coerce=int, validators=[DataRequired()])
    date = DateField("Date", validators=[DataRequired()])
    heure_debut = TimeField("Heure debut", validators=[DataRequired()])
    heure_fin = TimeField("Heure fin", validators=[DataRequired()])
    submit = SubmitField("Ajouter la presence")
