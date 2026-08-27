# Layers of the Internet — a hands-on walk

*Idea, 27 Aug 2026. Not a NornPulse feature — a separate project, parked here
so it doesn't evaporate.*

## The idea

A website or app that takes you hands-on through every stage of the internet
and of communication: from typing into a terminal, through HTML, CSS and
JavaScript, to modern solutions.

## What would make it different

Most "history of the web" material narrates. The thing worth building makes
you *feel* each layer's constraint, so the next layer lands as relief rather
than as a fact to memorise.

The mechanism: carry **one identical task** through every era. Same goal,
different century. The friction is the lesson.

| Era | You actually do | What it teaches |
|---|---|---|
| Raw socket | open a connection, type the protocol by hand | there is no magic — it is text over a pipe |
| Static document | write the HTML yourself | the web began as documents, not applications |
| Presentation | add CSS | separation of concerns arrived as a *discovery* |
| Behaviour | change the page without reloading | why this felt revolutionary at the time |
| Async | fetch without navigating | the tradeoff that created modern frontend |
| Realtime | push instead of poll | why polling was the bottleneck all along |

## The design rule

Each step should be *annoying enough* that the next one is a relief. Skipping
the annoyance is exactly what makes existing material forgettable — the
understanding lives in the friction, not in the summary of it.

Corollary: never show the modern solution first. The learner has to want it.

## Open questions

- How far back does "communication" go? Telegraph and packet-switching are
  the same story one layer down, and the same trick would work.
- Is it a guided path or a sandbox? A path teaches better; a sandbox gets
  revisited.
- Everything runs client-side if possible — a teaching tool that needs a
  backend is a teaching tool that stops working in two years.

## Why it sits next to NornPulse

Same instinct, different subject. NornPulse's argument is that a system should
show which of its claims are measured and which are assumed, rather than
presenting everything with flat confidence. This is the same move applied to
infrastructure: expose the layer instead of hiding it.
