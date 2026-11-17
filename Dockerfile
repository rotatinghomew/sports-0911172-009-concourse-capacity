FROM python:3.11-slim-bookworm@sha256:840e180ebcc6e5c8efab209c43f5e40fd2af98cb49db5c7103c90539c56bb30e
WORKDIR /app
COPY requirements-build.txt .
RUN python3 -m pip install --no-cache-dir -r requirements-build.txt
COPY . .
RUN sh scripts/verify.sh
CMD ["python3", "-m", "product", "demo"]
