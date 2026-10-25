# Rust Wildfire Physics Engine — Implementation Specification

Version 3 • October 24, 2026 • Scope: physics engine and its integration contracts.

The full normative specification is the project build plan supplied at implementation start. This file is the in-repo copy of that plan for provenance. Implement `surface_v1` as specified: Behave-style extended Rothermel local behavior, terrain-plane wind/slope combination, elliptical spread, first-order local Lax–Friedrichs level-set, explicit Euler, adaptive CFL events, and independent scenario ensembles.

This is an implementable research engine specification, not evidence that the resulting model is operationally validated. Do not describe this implementation as a complete FARSITE reproduction.
