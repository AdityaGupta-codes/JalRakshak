# PostgreSQL with the JalRakshak schema baked in. The init scripts run once, when the data volume is empty.
# (Copying them into the image instead of mounting them avoids problems with OneDrive / synced folders on Windows.)
FROM postgres:16
COPY schema.sql /docker-entrypoint-initdb.d/01_schema.sql
COPY seed.sql /docker-entrypoint-initdb.d/02_seed.sql
COPY migrate.sql /docker-entrypoint-initdb.d/03_migrate.sql
