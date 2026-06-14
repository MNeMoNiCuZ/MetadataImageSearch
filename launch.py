import sys
import os
import runpy

_root = os.path.dirname(os.path.abspath(__file__))
_src = os.path.join(_root, 'src')
os.chdir(_src)
sys.path.insert(0, _src)

runpy.run_path(os.path.join(_src, 'metadata_search.py'), run_name='__main__')
