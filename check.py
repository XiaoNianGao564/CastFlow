import urllib.request, json, re

BASE = 'http://localhost:3010'
print('=' * 50)
print('  CastFlow Quick Check')
print('=' * 50)
print()

# 1. API
try:
    r = urllib.request.urlopen(BASE + '/api/health', timeout=3)
    print('[1] API: OK')
except:
    print('[1] API: OFFLINE')
    print()
    print('请先启动服务:')
    print('  cd C:\\Users\\17166\\Desktop\\CastFlow\\packages\\api')
    print('  bun run src/index.ts')
    input('\n按回车退出...')
    exit()

# 2. 状态
r = urllib.request.urlopen(BASE + '/api/status', timeout=3)
s = json.loads(r.read().decode())
print(f'[2] 状态: {s["status"]}')
print(f'    版本: v{s["currentVersion"]}')
print(f'    准确率: {s["bestAccuracy"]}%')
print(f'    迭代: {s["totalIterations"]}次')

# 3. 最新记录
r = urllib.request.urlopen(BASE + '/api/history', timeout=3)
records = json.loads(r.read().decode())
if records:
    r = records[-1]
    code = r.get('code', '')
    preds = r.get('predictions', [])
    print(f'[3] 最新 v{r["version"]}:')
    print(f'    代码: {len(code)} 字符')
    models = re.findall(r'(SARIMAX|ARIMA|Prophet|XGBoost|ExponentialSmoothing|Holt)', code)
    if models: print(f'    模型: {set(models)}')
    print(f'    预测: {len(preds)} 条')
    for p in preds[:5]:
        print(f'      {p["org"]} {p["month"]} = {p["value"]}')
    ev = r.get('evaluation', {})
    if ev and 'accuracy' in ev:
        print(f'    准确率: {ev["accuracy"]}%  MAPE: {ev.get("mape","?")}%')
else:
    print('[3] 暂无记录')

print()
input('按回车退出...')
