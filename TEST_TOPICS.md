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

## Sleep ambient metadata samples

Use these title and description samples in the admin form. The topic drives the
story itself; the title and description present it as calm sleep-ambient
content.

| Topic | Title | Description | Hashtags |
| --- | --- | --- | --- |
| A lighthouse keeper notices one star missing from the sky. | The Lighthouse and the Missing Star — Sleep Ambient Story | Drift into a calm sleep ambient journey beside a quiet lighthouse, distant waves, and a sky holding one gentle mystery. | `#SleepAmbient #BedtimeStory #OceanSounds #Relaxation` |
| A quiet train makes its final journey through a sleeping countryside. | Midnight Train Through the Countryside — Sleep Ambient | Relax with a slow sleep ambient train journey through silent stations, moonlit fields, and peaceful nighttime scenery. | `#SleepAmbient #TrainSounds #DeepSleep #NightJourney` |
| An old clockmaker repairs a clock that remembers forgotten dreams. | The Clockmaker of Forgotten Dreams — Sleep Ambient Story | Unwind with a soft sleep ambient tale of ticking clocks, warm lamplight, and dreams returning one quiet memory at a time. | `#SleepStory #SleepAmbient #Dreams #CalmNarration` |
| A small boat follows a trail of lanterns across a moonlit lake. | Lanterns Across the Moonlit Lake — Sleep Ambient | Fall asleep to a gentle sleep ambient voyage across still water, glowing lanterns, and a peaceful moonlit horizon. | `#SleepAmbient #LakeSounds #Moonlight #RelaxingStory` |
| A gardener discovers a flower that only blooms at midnight. | The Midnight Garden — Sleep Ambient Story | Settle into a calming sleep ambient garden filled with soft night sounds, silver leaves, and one flower opening beneath the moon. | `#SleepAmbient #NightGarden #BedtimeStory #PeacefulSleep` |
| A traveler finds a peaceful hotel where every room overlooks a different season. | The Hotel of Four Seasons — Cozy Sleep Ambient Story | Rest inside a quiet old hotel where each room opens onto soft rain, autumn leaves, winter snow, or a warm summer evening. | `#SleepAmbient #CozyStory #RainSounds #DeepSleep` |
| A forest ranger discovers an abandoned observatory hidden among ancient trees. | The Observatory Hidden in the Forest — Sleep Ambient | Relax beneath a quiet sky as a forest ranger follows lantern light through ancient trees to a forgotten observatory. | `#SleepAmbient #ForestSounds #Stargazing #CalmStory` |
| A gentle river carries an elderly boatbuilder through places remembered from childhood stories. | The Boatbuilder's River of Memories — Sleep Ambient Story | Drift along a slow river through peaceful landscapes, warm memories, and familiar stories told beneath the evening sky. | `#SleepAmbient #RiverSounds #BedtimeStory #Relaxation` |
| A village librarian receives letters addressed to people from the past. | Letters from the Quiet Library — Sleep Ambient Mystery | Unwind in a warm village library where softly arriving letters lead to gentle memories from another time. | `#SleepAmbient #LibraryAmbience #CozyMystery #SleepStory` |
| A remote radio operator receives calm weather reports from a town that vanished decades ago. | Weather Reports from a Forgotten Town — Dark Sleep Ambient | Settle into a quiet radio room with soft static, distant rain, and a calm mystery arriving over the midnight airwaves. | `#DarkAmbient #SleepStory #RadioStatic #GentleMystery` |
| A silver-haired lighthouse keeper explores a weathered lighthouse from dusk until dawn. | A Night Inside the Old Lighthouse — Ocean Sleep Ambient | Fall asleep beside rolling waves and a distant foghorn while an old keeper makes one peaceful final walk through the lighthouse. | `#OceanAmbient #Lighthouse #SleepSounds #DeepSleep` |
| A woman carrying a red umbrella walks through an empty rain-covered city. | The Red Umbrella in the Sleeping City — Rain Sleep Ambient | Relax with steady rain, empty streets, glowing windows, and the quiet footsteps of a traveler crossing a city at night. | `#RainSounds #SleepAmbient #NightCity #Relaxation` |
| A black cat guides a young archivist through an enormous candlelit library. | The Black Cat of the Candlelit Library — Cozy Sleep Story | Follow a gentle black cat through quiet reading rooms, hidden staircases, warm candlelight, and shelves filled with forgotten tales. | `#CozySleep #LibraryAmbience #BedtimeStory #SleepAmbient` |
| A lighthouse keeper hears a second foghorn answering from beneath the sea. | The Foghorn Beneath the Sea — Dark Sleep Ambient Story | A slow, atmospheric sleep mystery with distant waves, heavy fog, and a second foghorn sounding softly beneath the water. | `#DarkSleepStory #OceanAmbient #GentleHorror #SleepNarration` |
| A night caretaker enters an abandoned cinema where the projector shows events that have not happened yet. | The Abandoned Cinema — Dark Sleep Ambient Story | A quiet dark sleep ambient mystery with an empty cinema, a softly humming projector, and gentle suspense without graphic or startling moments. | `#DarkAmbient #SleepStory #QuietHorror #OldCinema` |
| A traveler checks into an old hotel where every clock stops before someone knocks on an empty room's door. | The Hotel Where Every Clock Stops — Dark Sleep Story | Drift into a low-stimulation mystery of quiet hallways, ticking clocks, distant rain, and one room that should be empty. | `#DarkSleepStory #HotelAmbience #GentleHorror #BedtimeMystery` |

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
