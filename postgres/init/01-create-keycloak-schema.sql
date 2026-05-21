-- ═══════════════════════════════════════════════════════════════════════
-- Keycloak schema bootstrap
-- ═══════════════════════════════════════════════════════════════════════
-- Postgres voert alle .sql bestanden in /docker-entrypoint-initdb.d uit
-- bij de allereerste boot. Dit bestand maakt het 'keycloak' schema aan
-- waarin Keycloak zijn eigen tabellen kan beheren.
--
-- LET OP: dit script draait alleen wanneer de database voor het eerst
-- wordt geïnitialiseerd (leeg volume). Bij een bestaande database
-- gebeurt er niets.

CREATE SCHEMA IF NOT EXISTS keycloak;

-- Geef de sparki-gebruiker volledige rechten op het keycloak-schema
GRANT ALL PRIVILEGES ON SCHEMA keycloak TO sparki;
ALTER DEFAULT PRIVILEGES IN SCHEMA keycloak GRANT ALL ON TABLES TO sparki;
ALTER DEFAULT PRIVILEGES IN SCHEMA keycloak GRANT ALL ON SEQUENCES TO sparki;