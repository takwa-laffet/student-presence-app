CREATE DATABASE IF NOT EXISTS systeme_presence CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE systeme_presence;

CREATE TABLE IF NOT EXISTS formations (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nom_formation VARCHAR(200) NOT NULL,
    description TEXT NULL,
    total_duration_hours INT NOT NULL DEFAULT 40
);

CREATE TABLE IF NOT EXISTS eleves (
    id INT AUTO_INCREMENT PRIMARY KEY,
    nom VARCHAR(120) NOT NULL,
    prenom VARCHAR(120) NOT NULL,
    email VARCHAR(200) NOT NULL UNIQUE,
    numero VARCHAR(30) NULL
);

CREATE TABLE IF NOT EXISTS eleve_formations (
    eleve_id INT NOT NULL,
    formation_id INT NOT NULL,
    PRIMARY KEY (eleve_id, formation_id),
    CONSTRAINT fk_eleve_formations_eleve FOREIGN KEY (eleve_id) REFERENCES eleves(id) ON DELETE CASCADE,
    CONSTRAINT fk_eleve_formations_formation FOREIGN KEY (formation_id) REFERENCES formations(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS presences (
    id INT AUTO_INCREMENT PRIMARY KEY,
    eleve_id INT NOT NULL,
    formation_id INT NOT NULL,
    date DATE NOT NULL,
    heure_debut TIME NOT NULL,
    heure_fin TIME NOT NULL,
    CONSTRAINT fk_presences_eleve FOREIGN KEY (eleve_id) REFERENCES eleves(id) ON DELETE CASCADE,
    CONSTRAINT fk_presences_formation FOREIGN KEY (formation_id) REFERENCES formations(id) ON DELETE CASCADE,
    CONSTRAINT uq_presence_eleve_formation_date UNIQUE (eleve_id, formation_id, date)
);

ALTER TABLE presences
    ADD INDEX idx_presences_date (date),
    ADD INDEX idx_presences_formation_date (formation_id, date);


-- ======================================
-- INITIAL SEED DATA (idempotent inserts)
-- ======================================

INSERT INTO formations (nom_formation, description, total_duration_hours)
SELECT 'dev web', 'Formation developpement web', 40
WHERE NOT EXISTS (
    SELECT 1 FROM formations WHERE nom_formation = 'dev web'
);

INSERT INTO formations (nom_formation, description, total_duration_hours)
SELECT 'Python', 'Formation Python', 40
WHERE NOT EXISTS (
    SELECT 1 FROM formations WHERE nom_formation = 'Python'
);

INSERT INTO eleves (nom, prenom, email, numero)
VALUES ('Benothmen', 'Hafiza', 'benothmenhafiza@gmail.com', '+966 54 955 8601')
ON DUPLICATE KEY UPDATE
    nom = VALUES(nom),
    prenom = VALUES(prenom),
    numero = VALUES(numero);

INSERT INTO eleves (nom, prenom, email, numero)
VALUES ('Hafsi', 'Nourhenne', 'hafsinour97@gmail.com', '+216 94 260 794')
ON DUPLICATE KEY UPDATE
    nom = VALUES(nom),
    prenom = VALUES(prenom),
    numero = VALUES(numero);

INSERT INTO eleves (nom, prenom, email, numero)
VALUES ('Raissi', 'Mariem', 'mariem.raissi1190@gmail.com', '+216 20 720 262')
ON DUPLICATE KEY UPDATE
    nom = VALUES(nom),
    prenom = VALUES(prenom),
    numero = VALUES(numero);

INSERT IGNORE INTO eleve_formations (eleve_id, formation_id)
SELECT e.id, f.id
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'benothmenhafiza@gmail.com';

INSERT IGNORE INTO eleve_formations (eleve_id, formation_id)
SELECT e.id, f.id
FROM eleves e
JOIN formations f ON f.nom_formation = 'Python'
WHERE e.email = 'hafsinour97@gmail.com';

INSERT IGNORE INTO eleve_formations (eleve_id, formation_id)
SELECT e.id, f.id
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'mariem.raissi1190@gmail.com';


-- ======================================
-- SAMPLE PRESENCE DATA FOR HAFIZA
-- ======================================

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-07', '11:00', '14:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'benothmenhafiza@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-08', '11:00', '13:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'benothmenhafiza@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-23', '19:00', '21:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'benothmenhafiza@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-27', '10:00', '12:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'benothmenhafiza@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-28', '08:00', '10:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'benothmenhafiza@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-29', '18:00', '20:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'benothmenhafiza@gmail.com';


-- ======================================
-- SAMPLE PRESENCE DATA FOR NOURHENNE
-- ======================================

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-01', '10:00', '12:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'Python'
WHERE e.email = 'hafsinour97@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-15', '10:00', '12:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'Python'
WHERE e.email = 'hafsinour97@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-29', '10:00', '14:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'Python'
WHERE e.email = 'hafsinour97@gmail.com';


-- ======================================
-- SAMPLE PRESENCE DATA FOR MARIEM
-- ======================================

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-07', '11:00', '14:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'mariem.raissi1190@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-08', '11:00', '13:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'mariem.raissi1190@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-23', '19:00', '21:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'mariem.raissi1190@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-31', '19:22', '21:22'
FROM eleves e
JOIN formations f ON f.nom_formation = 'Python'
WHERE e.email = 'hafsinour97@gmail.com';

INSERT IGNORE INTO presences (eleve_id, formation_id, date, heure_debut, heure_fin)
SELECT e.id, f.id, '2026-03-31', '16:00', '18:00'
FROM eleves e
JOIN formations f ON f.nom_formation = 'dev web'
WHERE e.email = 'benothmenhafiza@gmail.com';


-- =====================================================
-- MIGRATION FOR EXISTING DATABASE (run once if needed)
-- =====================================================
-- Use this block when your database already exists and you want
-- one eleve to be linked to multiple formations.

-- START TRANSACTION;
--
-- CREATE TABLE IF NOT EXISTS eleve_formations (
--   eleve_id INT NOT NULL,
--   formation_id INT NOT NULL,
--   PRIMARY KEY (eleve_id, formation_id),
--   CONSTRAINT fk_eleve_formations_eleve FOREIGN KEY (eleve_id) REFERENCES eleves(id) ON DELETE CASCADE,
--   CONSTRAINT fk_eleve_formations_formation FOREIGN KEY (formation_id) REFERENCES formations(id) ON DELETE CASCADE
-- );
--
-- -- If old column exists, move data into mapping table
-- INSERT IGNORE INTO eleve_formations (eleve_id, formation_id)
-- SELECT id, formation_id
-- FROM eleves
-- WHERE formation_id IS NOT NULL;
--
-- -- Keep mapping synced with existing presence history
-- INSERT IGNORE INTO eleve_formations (eleve_id, formation_id)
-- SELECT DISTINCT eleve_id, formation_id
-- FROM presences;
--
-- -- Optional cleanup after migration
-- -- ALTER TABLE eleves DROP COLUMN formation_id;
--
-- -- Update unique rule to allow same eleve on same date in different formations
-- ALTER TABLE presences
--   DROP INDEX uq_presence_eleve_date,
--   ADD CONSTRAINT uq_presence_eleve_formation_date UNIQUE (eleve_id, formation_id, date);
--
-- COMMIT;
