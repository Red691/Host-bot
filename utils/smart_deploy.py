import json
import os


def detect_project_type(directory: str) -> str:
    files = os.listdir(directory)
    if any(f.endswith(".py") for f in files) or "requirements.txt" in files:
        return "python"
    if "package.json" in files:
        return "nodejs"
    if "go.mod" in files:
        return "golang"
    if "Gemfile" in files:
        return "ruby"
    if "pom.xml" in files or "build.gradle" in files:
        return "java"
    if "Cargo.toml" in files:
        return "rust"
    if "composer.json" in files:
        return "php"
    return "unknown"


def generate_dockerfile(project_type: str, directory: str) -> str:
    if project_type == "python":
        entry = "main.py"
        for candidate in ["main.py", "app.py", "bot.py"]:
            if os.path.exists(os.path.join(directory, candidate)):
                entry = candidate
                break
        return f"""FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "{entry}"]
"""
    elif project_type == "nodejs":
        start_cmd = "node index.js"
        pkg_path = os.path.join(directory, "package.json")
        if os.path.exists(pkg_path):
            with open(pkg_path) as f:
                pkg = json.load(f)
            scripts = pkg.get("scripts", {})
            if "start" in scripts:
                start_cmd = "npm start"
            elif "server.js" in files:
                start_cmd = "node server.js"
        return f"""FROM node:18-slim
WORKDIR /app
COPY package*.json .
RUN npm install
COPY . .
CMD {json.dumps(start_cmd.split())}
"""
    elif project_type == "golang":
        return """FROM golang:1.21-alpine
WORKDIR /app
COPY go.* .
RUN go mod download
COPY . .
RUN go build -o main .
CMD ["./main"]
"""
    elif project_type == "ruby":
        return """FROM ruby:3.2-slim
WORKDIR /app
COPY Gemfile* .
RUN bundle install
COPY . .
CMD ["ruby", "main.rb"]
"""
    elif project_type == "java":
        if os.path.exists(os.path.join(directory, "pom.xml")):
            return """FROM maven:3.9-eclipse-temurin-17-alpine
WORKDIR /app
COPY pom.xml .
RUN mvn dependency:go-offline
COPY . .
RUN mvn package -DskipTests
CMD ["java", "-jar", "target/app.jar"]
"""
        else:
            return """FROM gradle:8.5-jdk17-alpine
WORKDIR /app
COPY build.gradle .
RUN gradle dependencies
COPY . .
RUN gradle build
CMD ["java", "-jar", "build/libs/app.jar"]
"""
    elif project_type == "rust":
        return """FROM rust:1.75-slim
WORKDIR /app
COPY Cargo.toml .
RUN mkdir src && echo 'fn main() {}' > src/main.rs
RUN cargo build --release || true
COPY . .
RUN cargo build --release
CMD ["./target/release/app"]
"""
    elif project_type == "php":
        return """FROM php:8.2-cli
WORKDIR /app
COPY composer.json .
RUN composer install --no-dev
COPY . .
CMD ["php", "index.php"]
"""
    return ""
