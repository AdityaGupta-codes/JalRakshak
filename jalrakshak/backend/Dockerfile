# Build context is the repo root:  docker build -f backend/Dockerfile .
# Stage 1: compile the C++ scheduler
FROM gcc:13 AS scheduler-build
WORKDIR /src
COPY scheduler/ scheduler/
RUN make -C scheduler

# Stage 2: Python API + the compiled scheduler binary
FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app app
COPY --from=scheduler-build /src/scheduler/build/jalrakshak-scheduler /usr/local/bin/jalrakshak-scheduler
ENV SCHEDULER_BIN=/usr/local/bin/jalrakshak-scheduler
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
