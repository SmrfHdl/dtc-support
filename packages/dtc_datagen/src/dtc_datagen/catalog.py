"""Products and variants."""

from collections.abc import Sequence
from dataclasses import dataclass

from dtc_datagen.rng import Rng
from dtc_datagen.rows import Dataset, ProductRow, VariantRow


@dataclass(frozen=True)
class CategorySpec:
    code: str
    sku_prefix: str
    names: tuple[str, ...]
    sizes: tuple[str, ...]
    price_range: tuple[int, int]  # list price in cents, inclusive
    weight: float  # share of the random catalog


# Every code here must exist in the policy YAML (checked by a test).
CATEGORIES: tuple[CategorySpec, ...] = (
    CategorySpec(
        "apparel",
        "APP",
        ("Oxford Shirt", "Crew Tee", "Chino Pant", "Denim Jacket", "Hoodie", "Linen Shirt"),
        ("XS", "S", "M", "L", "XL"),
        (2500, 9800),
        0.45,
    ),
    CategorySpec(
        "footwear",
        "FTW",
        ("Runner", "Loafer", "Chelsea Boot", "Canvas Sneaker"),
        ("7", "8", "9", "10", "11", "12"),
        (6000, 16000),
        0.2,
    ),
    CategorySpec(
        "accessories",
        "ACC",
        ("Leather Belt", "Wool Scarf", "Canvas Tote", "Cap", "Wallet"),
        ("One Size",),
        (1500, 6000),
        0.15,
    ),
    CategorySpec(
        "underwear",
        "UND",
        ("Boxer Brief 3-Pack", "Ankle Socks 6-Pack", "Bralette"),
        ("S", "M", "L"),
        (1800, 4200),
        0.12,
    ),
    CategorySpec(
        "final_sale",
        "FIN",
        ("Sample Sale Jacket", "Archive Tee"),
        ("S", "M", "L"),
        (1500, 5000),
        0.08,
    ),
)
SPEC_BY_CODE = {c.code: c for c in CATEGORIES}
COLORS = ("Black", "Navy", "White", "Olive", "Sand", "Grey", "Burgundy")


def price(rng: Rng, lo: int, hi: int) -> int:
    """A store-like price in [lo, hi] cents, ending in 99 (e.g. 4999)."""
    return rng.randint((lo + 100) // 100, (hi + 1) // 100) * 100 - 1


def add_product(
    ds: Dataset,
    rng: Rng,
    *,
    sku: str,
    name: str,
    category_code: str,
    sizes: Sequence[str],
    colors: Sequence[str],
    list_price_cents: int,
    stock: int | None = None,
) -> list[VariantRow]:
    """Add a product with one variant per (size, color). Random stock unless given."""
    product = ProductRow(id=rng.uuid(), sku=sku, name=name, category_code=category_code)
    ds.products.append(product)
    variants = [
        VariantRow(
            id=rng.uuid(),
            product_id=product.id,
            size=size,
            color=color,
            list_price_cents=list_price_cents,
            stock=stock if stock is not None else (0 if rng.chance(0.1) else rng.randint(1, 60)),
        )
        for color in colors
        for size in sizes
    ]
    ds.variants.extend(variants)
    return variants


def random_catalog(ds: Dataset, rng: Rng, n_products: int) -> dict[str, list[list[VariantRow]]]:
    """Random products; returns category -> list of products (each a list of variants)."""
    by_category: dict[str, list[list[VariantRow]]] = {c.code: [] for c in CATEGORIES}
    for i in range(n_products):
        spec = rng.weighted(CATEGORIES, [c.weight for c in CATEGORIES])
        colors = rng.sample(COLORS, rng.randint(1, 3))
        variants = add_product(
            ds,
            rng,
            sku=f"{spec.sku_prefix}-{i + 1:04d}",
            name=f"{rng.choice(colors)} {rng.choice(spec.names)}",
            category_code=spec.code,
            sizes=spec.sizes,
            colors=colors,
            list_price_cents=price(rng, *spec.price_range),
        )
        by_category[spec.code].append(variants)
    return by_category
