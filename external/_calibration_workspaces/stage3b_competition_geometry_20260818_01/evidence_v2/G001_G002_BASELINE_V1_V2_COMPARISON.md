# G001/G002 Baseline–v1–v2 Comparison

G001 is primary acceptance evidence; G002 is robustness evidence. Truth appears only in this evaluation artifact.

| Sample | Target | Version | Support mm (error) | Height mm (error %) | Source / family |
|---|---|---:|---:|---:|---|
| G001 | lower_bottle | baseline | 736.3 (+11.3) | 145.0 (-19.5%) | point_p5_fallback |
| G001 | lower_bottle | candidate_v1 | 736.5 (+11.5) | 173.2 (-3.8%) | candidate_same_layer_consensus |
| G001 | lower_bottle | candidate_v2 | 717.1 (-7.9) | 187.5 (+4.2%) | same_surface_consensus / upright_slender |
| G001 | lower_volleyball | baseline | 747.3 (+22.3) | 147.7 (-24.3%) | point_p5_fallback |
| G001 | lower_volleyball | candidate_v1 | 736.5 (+11.5) | 192.3 (-1.4%) | candidate_same_layer_consensus |
| G001 | lower_volleyball | candidate_v2 | 717.1 (-7.9) | 190.7 (-2.2%) | same_surface_consensus / roughly_spherical |
| G001 | upper_bottle | baseline | 1101.8 (+36.8) | 128.2 (-28.8%) | point_p5_fallback |
| G001 | upper_bottle | candidate_v1 | 1086.7 (+21.7) | 146.0 (-18.9%) | candidate_contact_band |
| G001 | upper_bottle | candidate_v2 | 1075.0 (+10.0) | 162.5 (-9.7%) | local_gravity_support_surface / upright_slender |
| G002 | lower_bottle | baseline | 700.9 (-24.1) | 161.3 (-10.4%) | point_p5_fallback |
| G002 | lower_bottle | candidate_v1 | 716.3 (-8.7) | 177.7 (-1.3%) | candidate_same_layer_consensus |
| G002 | lower_bottle | candidate_v2 | 698.1 (-26.9) | 193.5 (+7.5%) | same_surface_consensus / upright_slender |
| G002 | lower_volleyball | baseline | 740.0 (+15.0) | 144.0 (-26.1%) | point_p5_fallback |
| G002 | lower_volleyball | candidate_v1 | 716.3 (-8.7) | 209.0 (+7.2%) | candidate_same_layer_consensus |
| G002 | lower_volleyball | candidate_v2 | 698.1 (-26.9) | 213.2 (+9.4%) | same_surface_consensus / roughly_spherical |
| G002 | upper_bottle | baseline | 1108.1 (+43.1) | 117.8 (-34.6%) | point_p5_fallback |
| G002 | upper_bottle | candidate_v1 | 1090.4 (+25.4) | 145.0 (-19.4%) | candidate_contact_band |
| G002 | upper_bottle | candidate_v2 | 1076.0 (+11.0) | 160.1 (-11.0%) | local_gravity_support_surface / upright_slender |

G001 v2: all support errors are within the preferred 20 mm band; all height errors are within the acceptable 10% band, with lower bottle and volleyball also within preferred 5%. G002 remains finite and structurally stable: support errors are within acceptable 50 mm; lower objects are within acceptable 10%; upper improves materially versus baseline/v1 but remains −11.0%.
