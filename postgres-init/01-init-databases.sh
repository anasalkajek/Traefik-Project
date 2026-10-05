#!/bin/bash
# Creates one database per microservice on first container start.
# The base image runs every .sh/.sql file here automatically on init.
set -e

for db in users_db products_db orders_db; do
      psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    SELECT 'CREATE DATABASE $db' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')\gexec
EOSQL
done
