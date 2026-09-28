FROM ghcr.io/cirruslabs/flutter:stable AS flutter
RUN flutter create --platforms web --project-name career_atlas /app/flutter-build
COPY flutter/pubspec.yaml /app/flutter-build/pubspec.yaml
COPY flutter/lib /app/flutter-build/lib
WORKDIR /app/flutter-build
RUN flutter pub get && flutter build web --release

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./
COPY --from=flutter /app/flutter-build/build/web ./web
ENV PORT=10000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
