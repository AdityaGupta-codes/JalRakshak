# Serves the prebuilt dashboard (public/) with nginx.  Build context: frontend/
FROM nginx:alpine
COPY public/ /usr/share/nginx/html/
# Security headers (CSP etc.); API_ORIGIN is substituted at start-up
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
ENV API_ORIGIN=http://localhost:8000
EXPOSE 80
