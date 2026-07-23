-- The read-only role DataMind connects with, mirroring the Postgres fixture.
-- SELECT only, so the connector's write-probe fails and readonly_confirmed
-- is true. mysql_native_password keeps aiomysql happy without TLS.
CREATE USER IF NOT EXISTS 'analytics_ro'@'%'
  IDENTIFIED WITH mysql_native_password BY 'analytics_ro';

GRANT SELECT ON sakila.* TO 'analytics_ro'@'%';
FLUSH PRIVILEGES;
