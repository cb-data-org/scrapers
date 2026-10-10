"""Geography: bairro polygons (data/seed/bairros) and point-in-polygon lookup."""

from .bairros import SEED_DIR, Bairro, BairroIndex, slugify

__all__ = ["SEED_DIR", "Bairro", "BairroIndex", "slugify"]
