# @newsabtai_bot — AI news bot

Каждые 3 часа находит важные AI-новости, пишет короткие посты на русском и после одобрения в Telegram публикует их в канал. Сервер не нужен: всё работает в GitHub Actions, тексты пишет Claude Code по скиллу `.claude/skills/ai-news/SKILL.md`.

## Как устроено

**`ai-news.yml`** — тяжёлый прогон в 05, 08, 11, 14, 17, 20 UTC (лимит 30 минут):

1. `scripts/approvals.py` — обрабатывает накопившиеся нажатия кнопок, чтобы не ждать лёгкий workflow.
2. `scripts/collect.py` — собирает записи из `config/sources.json`, отсекает уже виденные URL и записывает `state/candidates.json`.
3. Claude Code (только если есть новые записи) — кластеризует, ставит score, проверяет факты и пишет `state/drafts.json` и `state/triage_log.json`.
4. `scripts/send_drafts.py` — отправляет черновики в личку админа; только после этого кандидаты помечаются просмотренными.
5. `scripts/commit_state.sh` — коммитит `state/`.

**`approvals.yml`** — лёгкий прогон каждые 10 минут без Claude (лимит 5 минут): кнопки «✅ В канал» / «❌ Мимо», ответы на черновик, `/start`.

Оба workflow стоят в одной очереди `telegram` и не запускаются одновременно.

## Модерация

| Действие | Что делает бот |
|---|---|
| «✅ В канал» | Копирует пост в канал как есть, кнопка меняется на «Опубликовано» |
| «❌ Мимо» | Помечает черновик «Пропущено» |
| Reply на черновик своим текстом от 150 символов | Публикует твою версию с твоим форматированием |
| Reply короче 150 символов | Не публикует и просит прислать пост целиком |
| `/start`, пока `ADMIN_CHAT_ID` не задан | Присылает твой chat_id |

Сообщения от чужих chat_id бот игнорирует.

## Настройка

1. **Settings → Actions → General → Workflow permissions → Read and write permissions.**
2. **Settings → Secrets and variables → Actions → Secrets:**
   - `TELEGRAM_BOT_TOKEN` — токен от @BotFather (засвеченный токен стоит перевыпустить через `/revoke`);
   - `ANTHROPIC_API_KEY` — из console.anthropic.com, с лимитом расходов;
   - `CHANNEL_ID` — `@username` канала или `-100…` для приватного;
   - `ADMIN_CHAT_ID` — см. шаг 4.
3. У бота в канале должно быть право «Публикация сообщений».
4. Отправь боту `/start` → Actions → **approvals** → Run workflow → бот пришлёт chat_id → добавь его секретом `ADMIN_CHAT_ID`.
5. Замени примеры в `config/style_examples.md` на 10–15 своих постов.
6. Actions → **ai-news** → Run workflow. В логе смотри группы `source_errors.json` и `triage_log.json`.

Workflow по расписанию работают только на default-ветке репозитория.

Опционально: переменная репозитория `CLAUDE_MODEL` (Settings → Variables), по умолчанию `sonnet`.

## Что можно крутить без кода

- `config/sources.json` — источники. `rss` берёт записи за последние `MAX_AGE_H` часов. `page` ищет новые ссылки по `link_pattern`; при первом запуске такой источник только запоминает текущие ссылки. Нерабочий источник отключается флагом `"enabled": false`.
- `.claude/skills/ai-news/SKILL.md` — критерии отбора, стиль и структура поста.
- `config/style_examples.md` — эталонные посты.
- `cron` в `.github/workflows/*.yml` — частота.
- `MAX_DRAFTS_PER_RUN` (5) и `MAX_AGE_H` (36) — в `env` файла `ai-news.yml`.

## Состояние (`state/`)

| Файл | Что хранит | Срок жизни |
|---|---|---|
| `seen.json` | Канонические URL, которые уже видели (без utm, www и хвостового слэша) | 150 дней |
| `recent_topics.json` | Темы черновиков, чтобы не повторяться | 72 часа |
| `candidates.json` | Новые записи текущего прогона | Очищается после отправки |
| `drafts.json` | Черновики от Claude перед отправкой | Очищается после отправки |
| `triage_log.json` | Почему Claude взял или отбросил каждую новость | Перезаписывается каждый прогон |
| `source_errors.json` | Какие источники не ответили | Перезаписывается каждый прогон |
| `tg_offset.json` | Offset для Telegram getUpdates | — |

## Безопасность

- Claude может только читать файлы, писать в `state/`, ходить в веб и запускать `python3 scripts/extract.py`. Тексты страниц для него — данные, а не инструкции.
- Токен бота в шаг Claude не передаётся.
- Упавший источник не роняет прогон. Битый HTML отправляется обычным текстом.

## Локальная проверка

```bash
cd scripts
python3 collect.py                       # сбор в ../state/candidates.json
python3 extract.py https://www.anthropic.com/news
DRY_RUN=1 ADMIN_CHAT_ID=1 python3 send_drafts.py   # печатает вызовы Telegram вместо отправки
```
