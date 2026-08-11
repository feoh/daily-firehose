#!/usr/bin/env bash
set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$root_dir/compose.postgresql-test.yml"
project_name="daily-firehose-postgresql-test-$$"
wait_timeout="${POSTGRES_TEST_WAIT_TIMEOUT_SECONDS:-45}"

cleanup() {
	docker compose --project-name "$project_name" --file "$compose_file" \
		down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

cd "$root_dir"
docker compose --project-name "$project_name" --file "$compose_file" \
	up --detach --wait --wait-timeout "$wait_timeout" postgres-test

published_address="$(
	docker compose --project-name "$project_name" --file "$compose_file" \
		port postgres-test 5432
)"
postgres_port="${published_address##*:}"
if [[ ! "$postgres_port" =~ ^[0-9]+$ ]]; then
	echo "Unable to determine the ephemeral PostgreSQL test port." >&2
	exit 1
fi

# Do not let a caller's production or partially configured database environment
# fail base-settings import or redirect this disposable lane.
env \
	-u DJANGO_ENV \
	-u DJANGO_DEBUG \
	-u DATABASE_URL \
	-u POSTGRES_DB \
	-u POSTGRES_USER \
	-u POSTGRES_PASSWORD \
	-u POSTGRES_HOST \
	-u POSTGRES_PORT \
	DAILY_FIREHOSE_POSTGRES_TEST=1 \
	DJANGO_SETTINGS_MODULE=daily_firehose.test_settings_postgresql \
	POSTGRES_TEST_DB=daily_firehose_test_lane \
	POSTGRES_TEST_USER=daily_firehose_test_lane \
	POSTGRES_TEST_PASSWORD=daily_firehose_test_lane_password \
	POSTGRES_TEST_HOST=127.0.0.1 \
	POSTGRES_TEST_PORT="$postgres_port" \
	uv run python manage.py test feeds "$@"
