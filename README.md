# 🏓 Edge Pong

Swing a real paddle at virtual balls — and feel every hit.

Weekend hackathon build, in progress.

## How it plays

Balls come at you off the wall. Swing the real paddle to meet them — the game
watches the ball's whole path between frames, not just snapshots, so even a
fast one can't slip through the paddle.

Connect, and the ball rebounds off the angle of your paddle: tilt the face to
aim your return. Land it on the far side to score. Miss, and the rally's over.

## The fun math: quaternions

The paddle can point any direction in 3D, so we track its orientation with
**quaternions** — four numbers that mean "spin by *this* angle, around *this*
axis."

Why not plain pitch / yaw / roll? Those jam up when two axes line up ("gimbal
lock") and blend badly mid-motion. Quaternions never jam and blend smoothly —
exactly what you want for a paddle whipping through a swing.
