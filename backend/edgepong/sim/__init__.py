"""Simulation ground-truth model shared by the mock camera and mock paddle.

In hardware mode these mocks are replaced by the real camera + ESP32, but the
rest of the pipeline (fusion, physics, collision, haptics, renderer) is
identical, so the simulation exercises the real code paths end-to-end.
"""
