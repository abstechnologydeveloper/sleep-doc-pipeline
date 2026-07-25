# Pipeline Test Topics

Use these topics to test script, audio, image, caption, transition, and final
video generation. Start with a one-minute smoke test before spending credits on
longer runs.

## One-minute smoke tests

Each topic should produce one script, one audio file, approximately three scene
images, and one short captioned video.

1. A lighthouse keeper notices one star missing from the sky.
2. A quiet train makes its final journey through a sleeping countryside.
3. An old clockmaker repairs a clock that remembers forgotten dreams.
4. A small boat follows a trail of lanterns across a moonlit lake.
5. A gardener discovers a flower that only blooms at midnight.
6. A night caretaker enters an abandoned cinema where the projector begins
   showing a quiet horror film about events that have not happened yet.

Example:

```bash
/Users/apple/.pyenv/versions/3.12.0/bin/python run_pipeline.py \
  "A lighthouse keeper notices one star missing from the sky" 1
```

## Five-minute story tests

1. A cartographer maps an island that slowly changes shape every night.
2. A village librarian receives letters addressed to people from the past.
3. A traveler finds a peaceful hotel where every room overlooks a different
   season.
4. A retired astronomer follows a dim blue light across an empty coastal town.
5. A forest ranger discovers an abandoned observatory hidden among ancient
   trees.

These runs should test multiple images, proportional scene timing, crossfades,
and longer caption synchronization.

## Ten-minute production tests

1. The last lighthouse keeper watches the stars disappear one by one while
   searching for the forgotten promise that can bring them back.
2. An overnight train crosses a country where every silent station preserves
   one memory from its final passenger.
3. A historian enters a deserted mountain monastery whose bells continue to
   ring despite having no ropes or inhabitants.
4. A gentle river carries an elderly boatbuilder through places that existed
   only in stories told during childhood.
5. A remote radio operator receives calm weather reports from a town that
   vanished many decades ago.

## Visual consistency tests

Use these to evaluate whether recurring people and locations remain visually
consistent between scene images.

1. A silver-haired lighthouse keeper in a navy coat explores the same weathered
   lighthouse from dusk until dawn.
2. A woman carrying a red umbrella walks through several districts of an empty
   rain-covered city.
3. A black cat guides a young archivist through different rooms of an enormous
   candlelit library.

## Atmosphere tests

1. Calm coastal mystery with fog, distant lanterns, and muted blue light.
2. Warm woodland story with an amber cabin, falling leaves, and a quiet stream.
3. Snow-covered observatory beneath a pale green aurora.
4. Ancient desert road illuminated by moonlight and small campfires.
5. Peaceful underwater ruins filled with soft light and slow-moving plants.

## Horror story tests

Keep the narration atmospheric and unsettling rather than loud, violent, or
graphic so it remains suitable for a sleep-story channel.

1. A night caretaker enters an abandoned cinema where the projector shows
   quiet scenes from events that have not happened yet.
2. A traveler checks into an old hotel and discovers that every clock stops at
   the same hour shortly before someone knocks on an empty room's door.
3. A lighthouse keeper hears a second foghorn answering from beneath the sea,
   although no other lighthouse appears on any map.
4. A librarian working after midnight notices that one book rewrites itself to
   describe each room she is about to enter.
5. The final passenger on a nearly empty train realizes that each station is a
   place remembered from a recurring childhood nightmare.
6. A radio host receives calls from residents of a town that disappeared from
   every map fifty years earlier.
7. A photographer develops old film showing a silent figure standing closer
   to the camera in every successive picture.
8. A forest ranger follows warm lantern light to a village where no resident
   casts a shadow.
9. An astronomer discovers a dark star that appears only when nobody else is
   looking through the telescope.
10. A caretaker in a mountain museum hears footsteps moving through exhibits
    that have been sealed for decades.

Example one-minute horror test:

```bash
/Users/apple/.pyenv/versions/3.12.0/bin/python run_pipeline.py \
  "A lighthouse keeper hears a second foghorn answering from beneath the sea" 1
```

## Validation checklist

- The saved script respects the requested duration.
- Narration contains audible speech and ends naturally.
- Images are 16:9 and contain no text or watermark.
- Existing images are skipped when generation resumes.
- Captions are readable and approximately synchronized with narration.
- Crossfades are gentle and do not shorten the final video.
- The final MP4 contains both H.264 video and AAC audio.
- The final video duration closely matches the narration duration.
- Failed stages preserve reusable outputs and can continue with `--resume`.

Resume the newest incomplete project with:

```bash
/Users/apple/.pyenv/versions/3.12.0/bin/python run_pipeline.py --resume
```
