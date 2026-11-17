FROM python:3.11-slim-bookworm@sha256:840e180ebcc6e5c8efab209c43f5e40fd2af98cb49db5c7103c90539c56bb30e
WORKDIR /app
COPY . .
RUN python3 -m unittest discover -s tests -v && python3 -m compileall -q product tests
CMD ["python3", "-m", "product", "demo"]
