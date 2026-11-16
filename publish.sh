#!/bin/bash
# Первая публикация проекта на GitHub:
#   1) репозиторий kg-realty (публичный) с кодом — ветка main
#   2) текущая база — сжатой в ветку data (оттуда её берут GitHub Actions и дашборд)
#   3) пробный запуск сбора на серверах GitHub
set -euo pipefail
cd "$(dirname "$0")"

echo "== Проверка входа в GitHub"
gh auth status

if ! git config user.name >/dev/null; then
  git config --global user.name "$(gh api user -q .login)"
fi
if ! git config user.email >/dev/null; then
  git config --global user.email "$(gh api user -q .id)+$(gh api user -q .login)@users.noreply.github.com"
fi

echo "== Код → ветка main"
[ -d .git ] || git init -q -b main
git add .
git commit -q -m "Парсер и дашборд рынка недвижимости Кыргызстана" || echo "(нечего коммитить)"
if ! git remote get-url origin >/dev/null 2>&1; then
  gh repo create kg-realty --public --source . --push
else
  git push -u origin main
fi
REPO_URL="$(gh repo view --json url -q .url)"
OWNER_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

echo "== База → ветка data"
uv sync -q
uv run python -m kg_realty export out/realty.db.gz
rm -rf out/.git
(
  cd out
  git init -q -b data
  git add realty.db.gz
  git commit -q -m "начальная база"
  git push -q --force "$REPO_URL.git" data
)

echo "== Пробный сбор на серверах GitHub"
gh workflow run collect.yml -f mode=fresh || echo "Запустите вручную: вкладка Actions → collect → Run workflow"

echo
echo "Готово."
echo "Репозиторий:  $REPO_URL"
echo "DATA_URL для Streamlit:  https://raw.githubusercontent.com/$OWNER_REPO/data/realty.db.gz"
