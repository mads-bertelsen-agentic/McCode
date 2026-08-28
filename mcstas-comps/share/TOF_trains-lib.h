/* TOF_train support macros
 *
 * T_ABSORB/T_TRANSMIT + TRAIN_GATE + TRAIN_READ for components manipulating
 * the per-ray TOF train (cogen contract globals: N_trains, N_active,
 * t_offset, p_trains, P_last_time_manipulation, adaptive_N, total_*).
 *
 * Pure #defines only: safe to %include from a component SHARE section
 * because expansion happens inside TRACE, where cogen's TOF_TRAIN
 * defines (bare names -> _particle members on GPU) are active.
 *
 * When TOF_TRAIN is not defined, the macros degrade to the plain
 * single-neutron case, so components using them behave exactly as before
 * the TOF_TRAIN optimization. In a TOF_TRAIN build, rays that carry no
 * train state (N_active == 0, e.g. emitted by a non-TOF source) are also
 * handled as plain single neutrons.
 */

#ifdef TOF_TRAIN

#define T_ABSORB() do { WT_absorb_ = 1; } while (0)
#define T_TRANSMIT() do { WT_absorb_ = 0; } while (0)

#define TRAIN_GATE(BODY_CODE)                                                                     \
  do {                                                                                            \
    if (N_active > 0) {                                                                           \
      double weight_update = p / P_last_time_manipulation;                                        \
      P_last_time_manipulation = 0;                                                               \
      double t_original = t;                                                                      \
      int train_index;                                                                            \
      for (train_index = 0; train_index < N_active;) {                                            \
        /* Expose readable names to BODY_CODE */                                                  \
        t = t_original + t_offset[train_index];                                                   \
        int WT_absorb_ = 0;                                                                       \
        BODY_CODE                                                                                 \
        if (WT_absorb_) {                                                                         \
          /* swap-with-last-active */                                                             \
          p_trains[train_index] = p_trains[N_active - 1];                                         \
          t_offset[train_index] = t_offset[N_active - 1];                                         \
          N_active--;                                                                             \
        }                                                                                         \
        else {                                                                                    \
          p_trains[train_index] *= weight_update;                                                 \
          P_last_time_manipulation += p_trains[train_index];                                      \
          train_index++;                                                                          \
        }                                                                                         \
      }                                                                                           \
      if (!N_active) ABSORB;                                                                      \
      p = P_last_time_manipulation;                                                               \
      t = t_original;                                                                             \
    }                                                                                             \
    else {                                                                                        \
      /* Ray carries no TOF train state: plain single-neutron case */                             \
      int WT_absorb_ = 0;                                                                         \
      BODY_CODE                                                                                   \
      if (WT_absorb_) ABSORB;                                                                     \
    }                                                                                             \
  } while (0)

#define TRAIN_READ(BODY_CODE)                                                                     \
  do {                                                                                            \
    if (N_active > 0) {                                                                           \
      int train_index;                                                                            \
      double p_original = p;                                                                      \
      double t_original = t;                                                                      \
      double p_factor = p / P_last_time_manipulation;                                             \
      for (train_index = 0; train_index < N_active; train_index++) {                              \
        /* Expose readable names to BODY_CODE */                                                  \
        p = p_factor * p_trains[train_index];                                                     \
        t = t_original + t_offset[train_index];                                                   \
        BODY_CODE                                                                                 \
      }                                                                                           \
      p = p_original;                                                                             \
      t = t_original;                                                                             \
    }                                                                                             \
    else {                                                                                        \
      /* Ray carries no TOF train state: plain single-neutron case */                             \
      BODY_CODE                                                                                   \
    }                                                                                             \
  } while (0)

#else /* TOF_TRAIN not defined: plain single-neutron case */

#define T_ABSORB() ABSORB
#define T_TRANSMIT() do { } while (0)
#define TRAIN_GATE(BODY_CODE) BODY_CODE
#define TRAIN_READ(BODY_CODE) BODY_CODE

#endif /* TOF_TRAIN */
