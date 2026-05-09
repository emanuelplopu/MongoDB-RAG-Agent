import json, sys
data = json.load(sys.stdin)
for m in data['models']:
    if 'gemma4' in m['name']:
        print(f"{m['name']:30s} {m['size']/1e9:.1f}GB")
