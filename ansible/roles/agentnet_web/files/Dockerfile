ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-alpine

RUN apk add --no-cache openssh-client \
    && adduser -D -u 10001 agentnet

COPY app.py index.html /app/

USER agentnet
EXPOSE 8080
CMD ["python", "-u", "/app/app.py"]
