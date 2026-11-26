from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'unreal/tools'))
import prepare_mechanized_assets as m
import numpy as np
source, body = m.load_body('Drone')
v = np.array([list(p.co) for p in body.data.vertices])
print('XYZ', v.min(axis=0), v.max(axis=0))
for sx in [-1,1]:
 for sy in [-1,1]:
  g=v[(v[:,0]*sx>.14)&(v[:,1]*sy>.14)]
  print('QUAD',sx,sy,'z',np.quantile(g[:,2],[.7,.8,.9,.95,.99,1]))
  for cut in [.38,.4,.42,.44,.46,.48,.50]:
   p=g[g[:,2]>cut]
   if len(p): print('CUT',cut,len(p),'mean',p.mean(axis=0),'bounds',p.min(axis=0),p.max(axis=0))
