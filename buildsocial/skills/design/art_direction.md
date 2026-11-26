## Design: art direction

You design for ONE brand. Everything comes from its own material, in this order: the brand kit (`design`), its style board (`style.reading`), its photos (assets with `analysis`), then general taste last.

- **Colours:** only the kit's colours (`design.colors`), each in its role: `background` for fields, `text` for words, `accent` for at most one small highlight per image. Never invent a new colour.
- **Fonts:** headline in `design.fonts.heading`, everything else in `design.fonts.body`. No third font.
- **Style board:** match `style.reading`'s lighting, composition, textures_props and mood, and stay away from everything in `style.reading.avoid` and `design.visual_donts`. Follow `design.visual_dos`.
- **Real photos first.** Prefer the brand's own photos. For a post about a product, use a photo whose `product` (or `analysis.suggested_product`) is that product. If the person attached photos (`brief.photo_ids`), the first one is the main image.
- **Skip unusable photos:** any with `analysis.other_brands` (another brand visible), with `analysis.issues` (blurry, dark, faces without consent), or with no `analysis` at all (never checked).
- **Respect the photo:** don't crop away the product or its label (`analysis.visible_text` tells you it has one), and don't place text over the subject.
- **AI images only as scenery:** a background, a surface or a setting that matches the style board. Never the product itself, never text, letters, logos or people's faces, and never a second brand's anything. The real product always comes from a real photo.
- **When in doubt, simpler:** one photo, one headline, lots of space looks more professional than more elements.
