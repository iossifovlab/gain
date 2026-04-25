ARG NODE_VERSION=22.14.0-alpine
ARG COMPOSE_PROJECT_NAME=gpfwa

FROM node:${NODE_VERSION} AS builder

# Set the working directory inside the container
WORKDIR /app

# Copy package-related files first to leverage Docker's caching mechanism
COPY package.json ./

# Install project dependencies using npm ci (ensures a clean, reproducible install)
RUN --mount=type=cache,target=/root/.npm npm i

# Copy the rest of the application source code into the container
COPY . .

# Build the Angular application
RUN npm run build 


FROM ${COMPOSE_PROJECT_NAME}-ubuntu-image AS runtime

RUN mkdir -p /logs
RUN mkdir -p /site

COPY --chown=www-data:www-data --from=builder /app/dist/*/browser /site/gpf-web-annotation

WORKDIR /site


# configure apache2
RUN mkdir -p /var/run/apache2

ENV APACHE_LOG_DIR=/var/log/apache2
ENV APACHE_LOCK_DIR=/var/lock/apache2
ENV APACHE_RUN_GROUP=www-data
ENV APACHE_RUN_DIR=/var/run/apache2
ENV APACHE_RUN_USER=www-data
ENV APACHE_PID_FILE=/var/run/apache2/apache2.pid

COPY ./scripts/localhost.conf /etc/apache2/sites-available/

RUN cat /etc/hostname | awk '{print "ServerName "$1}' >> /etc/apache2/apache2.conf
RUN echo "SetEnv proxy-initial-not-pooled 1" >> /etc/apache2/apache2.conf

RUN rm -f /etc/apache2/sites-enabled/000-default.conf

COPY ./scripts/supervisord.conf /etc/
COPY ./scripts/supervisord-bootstrap.sh /
COPY ./scripts/wait-for-it.sh /
RUN chmod +x /*.sh

EXPOSE 80 443

ENTRYPOINT ["supervisord", "-c", "/etc/supervisord.conf", "-n"]
