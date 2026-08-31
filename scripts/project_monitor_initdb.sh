#!/bin/sh
# Creates the test database beside the operator one, so a fresh machine gets the
# two-database arrangement `get_project_monitor_database_url` assumes rather than
# a hand-made database that exists only where someone remembered to create it.
#
# Runs once, on an empty data directory only (postgres image convention), so it
# is inert against the existing operator volume.
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
	CREATE DATABASE project_monitor_test;
SQL
