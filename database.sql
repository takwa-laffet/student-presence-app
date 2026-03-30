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
    numero VARCHAR(30) NULL,
    formation_id INT NULL,
    INDEX idx_eleves_formation_id (formation_id)
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
    CONSTRAINT uq_presence_eleve_date UNIQUE (eleve_id, date)
);

ALTER TABLE eleves
    ADD CONSTRAINT fk_eleves_formation
    FOREIGN KEY (formation_id) REFERENCES formations(id)
    ON UPDATE CASCADE
    ON DELETE SET NULL;

ALTER TABLE presences
    ADD INDEX idx_presences_date (date),
    ADD INDEX idx_presences_formation_date (formation_id, date);


-- =====================================================
-- MIGRATION FOR EXISTING DATABASE (run once if needed)
-- =====================================================
-- Use this block when your database already exists and you want
-- to add formation assignment directly on students.

-- START TRANSACTION;
--
-- ALTER TABLE eleves
--   ADD COLUMN numero VARCHAR(30) NULL AFTER email;
--
-- ALTER TABLE eleves
--   ADD COLUMN formation_id INT NULL AFTER numero;
--
-- ALTER TABLE formations
--   ADD COLUMN total_duration_hours INT NOT NULL DEFAULT 40 AFTER description;
--
-- UPDATE formations
-- SET total_duration_hours = 40
-- WHERE total_duration_hours IS NULL
--    OR total_duration_hours < 1;
--
-- UPDATE eleves e
-- SET e.formation_id = (
--   SELECT p.formation_id
--   FROM presences p
--   WHERE p.eleve_id = e.id
--   ORDER BY p.date DESC, p.heure_fin DESC, p.id DESC
--   LIMIT 1
-- );
--
-- ALTER TABLE eleves
--   ADD INDEX idx_eleves_formation_id (formation_id);
--
-- ALTER TABLE eleves
--   ADD CONSTRAINT fk_eleves_formation
--   FOREIGN KEY (formation_id) REFERENCES formations(id)
--   ON UPDATE CASCADE
--   ON DELETE SET NULL;
--
-- ALTER TABLE presences
--   ADD INDEX idx_presences_date (date),
--   ADD INDEX idx_presences_formation_date (formation_id, date);
--
-- COMMIT;
