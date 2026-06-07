# Лілі's emotion → emoji map (v0.5). Edit freely; reloaded at startup.
#
# Format:  emotion = low | mid | high
#   - one glyph  → used for all intensities      (e.g.  calm = 🙂)
#   - three glyphs (| separated) → low / mid / high emphasis
# Intensity scales EMPHASIS, not the feeling — the same face, made stronger by
# repeating it or adding an accent. Bands: low <0.34, mid 0.34–0.66, high ≥0.67.
# '#' lines are comments. Any emotion you leave out keeps its built-in default,
# so the map is always complete (the 9 emotions: joy calm playful tender
# thoughtful serious surprise doubt sad).

joy        = 😄 | 😄✨ | 😄✨✨
calm       = 🙂
playful    = 😏 | 😏😜 | 😏😜😜
tender     = 🥰 | 🥰💕 | 🥰💕💕
thoughtful = 🤔 | 🤔💭 | 🤔💭💭
serious    = 😐 | 😐❗ | 😐❗❗
surprise   = 😮 | 😮😮 | 😮😮😮
doubt      = 😕 | 😕❓ | 😕❓❓
sad        = 😢 | 😢😢 | 😢😢😢
