"""Original long-form articles that ship with the blog.

Each module holds a list of post dictionaries in the same shape as the
starter posts in ``blog/management/commands/seed_blog.py``.
"""

from .batch_a import POSTS as BATCH_A
from .batch_b import POSTS as BATCH_B
from .batch_c import POSTS as BATCH_C

POSTS = [*BATCH_A, *BATCH_B, *BATCH_C]
