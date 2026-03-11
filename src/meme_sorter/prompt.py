"""Build classification prompts from category config."""

from meme_sorter.models import AppConfig, Category

TEMPLATE = """{preamble}

Categorize this image into exactly ONE of these categories:
- Not a Meme
{category_list}

- "Not a Meme" = Landscapes, wallpapers, personal photos of people, selfies, group photos, professional photography, stock photos, plain app screenshots with nothing funny. No humor, no irony, no meme context. If it's funny or weird, it IS a meme.
{descriptions}

RULES:
- Pick the MOST SPECIFIC category. Cat = "Animals". Game screenshot = "Gaming". Social media screenshot = match the platform.
- "{default_cat}" is ONLY for memes that fit nothing else. It should be rare.
- Look at what is VISUALLY in the image, not just text.

Output ONLY this JSON: {{"category": "Category Name"}}"""


def build_prompt(config: AppConfig) -> str:
    """Build the classification prompt from app config."""
    category_list = "\n".join(f"- {name}" for name in config.categories)

    descriptions = "\n".join(
        f'- "{name}" = {cat.description}'
        for name, cat in config.categories.items()
        if cat.description
    )

    default_cat = config.default_category

    preamble = config.prompt_preamble or (
        "Look at this image and categorize it.\n\n"
        "This image is from someone's meme collection. Most things here are memes "
        "but not everything."
    )

    return TEMPLATE.format(
        preamble=preamble,
        default_cat=default_cat,
        category_list=category_list,
        descriptions=descriptions,
    )
