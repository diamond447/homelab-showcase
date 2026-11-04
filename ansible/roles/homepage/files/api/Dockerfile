ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-alpine

RUN adduser -D -u 10001 homepage-api

COPY app.py agent.html /app/

USER homepage-api
EXPOSE 8080
CMD ["python", "-u", "/app/app.py"]
