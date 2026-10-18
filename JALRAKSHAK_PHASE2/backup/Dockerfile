# Scheduled database backups (pg_dump). The script is copied into the image instead of mounted, which avoids
# problems with OneDrive / synced folders on Windows.
FROM postgres:16
COPY backup.sh /usr/local/bin/jal-backup.sh
RUN chmod +x /usr/local/bin/jal-backup.sh
ENTRYPOINT ["/usr/local/bin/jal-backup.sh"]
