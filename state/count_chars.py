import re
with open('/home/runner/work/ainews_bot/ainews_bot/state/.draft1.txt', encoding='utf-8') as f:
    text = f.read()
visible = re.sub(r'<[^>]+>', '', text)
print('total', len(text))
print('visible', len(visible))
