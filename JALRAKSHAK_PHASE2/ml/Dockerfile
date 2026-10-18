# Data-science pipeline: fills consumption, predictions, model_evaluations and alerts, then exits.
# docker compose runs it once after the database is healthy.  Build context: the project folder.
FROM python:3.12-slim
WORKDIR /ml
COPY ml/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ml/jalml jalml
COPY ml/tests tests
COPY db/migrate.sql /db/migrate.sql
CMD ["python", "-m", "jalml.pipeline", "--out", "/tmp/results"]
