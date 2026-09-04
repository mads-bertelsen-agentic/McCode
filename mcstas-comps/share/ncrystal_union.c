/*******************************************************************************
 * Shared NCrystal implementation for Union components.
 ******************************************************************************/

#ifndef NCRYSTAL_UNION_C
#define NCRYSTAL_UNION_C

#if defined(WIN32) || defined(_WIN32)
#include "NCrystal\\ncrystal.h"
#else
#include "NCrystal/ncrystal.h"
#endif
#include "stdio.h"
#include "stdlib.h"

#ifndef NCMCERR2
#define NCMCERR2(compname, msg)                                                                                              \
  do {                                                                                                                       \
    fprintf (stderr, "\nNCrystal: %s: ERROR: %s\n\n", compname, msg);                                                     \
    exit (1);                                                                                                                \
  } while (0)
#endif

static int ncsample_reported_version_union = 0;

typedef struct {
  double density_factor;
  double inv_density_factor;
  ncrystal_scatter_t scat;
  ncrystal_process_t proc_scat, proc_abs;
  int proc_scat_isoriented;
  int absmode;
} ncrystalsample_t_union;

struct NCrystal_physics_storage_struct {
  ncrystalsample_t_union stored_params;
  double ncrystal_convfact_vsq2ekin;
  double ncrystal_convfact_ekin2vsq;
};

static void
ncrystal_union_check_version (const char *component_name)
{
  if (NCRYSTAL_VERSION != ncrystal_version ())
    NCMCERR2 (component_name, "Inconsistency detected between included ncrystal.h and linked NCrystal library!");
  if (ncsample_reported_version_union != ncrystal_version ()) {
    if (ncsample_reported_version_union)
      NCMCERR2 (component_name, "Inconsistent NCrystal library versions detected - this should normally not be possible!");
    ncsample_reported_version_union = ncrystal_version ();
    MPI_MASTER (printf ("NCrystal: McStas Union component(s) are using version %s of the NCrystal library.\n", ncrystal_version_str ()););
  }
}

static void
ncrystal_union_setup_conversion (double *vsq2ekin, double *ekin2vsq)
{
  /* 0.0253302959105844428609698658024319097260896937 is 1/(4*pi^2). */
  *vsq2ekin = ncrystal_wl2ekin (1.0) * 0.0253302959105844428609698658024319097260896937 / (K2V * K2V);
  *ekin2vsq = 1.0 / *vsq2ekin;
}

static void
ncrystal_union_setup_rng (void)
{
#ifndef rand01
  ncrystal_setrandgen (rand01);
#else
  ncrystal_setbuiltinrandgen_withseed (mcseed);
#endif
}

static double
ncrystal_union_get_numberdensity (ncrystal_info_t info, const char *component_name)
{
  double numberdensity;

#if NCRYSTAL_VERSION >= 1099001
  numberdensity = ncrystal_info_getnumberdensity (info);
#else
  unsigned cell_sg, cell_atnum;
  double cell_a, cell_b, cell_c, cell_alpha, cell_beta, cell_gamma, cell_volume;
  if (!ncrystal_info_getstructure (info, &cell_sg, &cell_a, &cell_b, &cell_c, &cell_alpha, &cell_beta, &cell_gamma, &cell_volume, &cell_atnum))
    NCMCERR2 (component_name, "Structure information is unavailable in the loaded NCrystal Info");
  numberdensity = cell_atnum / cell_volume;
#endif

  if (numberdensity <= 0.0)
    NCMCERR2 (component_name, "Number density information is unavailable in the loaded NCrystal Info");
  return numberdensity;
}

int
NCrystal_physics_my (double *my, double *k_initial, union data_transfer_union data_transfer, struct focus_data_struct *focus_data, _class_particle *_particle)
{
  double k_mag = sqrt (k_initial[0] * k_initial[0] + k_initial[1] * k_initial[1] + k_initial[2] * k_initial[2]);
  double dir[3];
  dir[0] = k_initial[0] / k_mag;
  dir[1] = k_initial[1] / k_mag;
  dir[2] = k_initial[2] / k_mag;

  double vsq2ekin = data_transfer.pointer_to_a_NCrystal_physics_storage_struct->ncrystal_convfact_vsq2ekin;
  double ekin = vsq2ekin * K2V * k_mag * K2V * k_mag;
  double xsect_scat = 0.0;
  ncrystal_crosssection (data_transfer.pointer_to_a_NCrystal_physics_storage_struct->stored_params.proc_scat, ekin, (const double (*)[3]) &dir, &xsect_scat);

  double density_factor = data_transfer.pointer_to_a_NCrystal_physics_storage_struct->stored_params.density_factor;
  *my = xsect_scat / density_factor;
  return 1;
}

int
NCrystal_physics_scattering (double *k_final, double *k_initial, double *weight, union data_transfer_union data_transfer,
                             struct focus_data_struct *focus_data, _class_particle *_particle)
{
  double k_mag = sqrt (k_initial[0] * k_initial[0] + k_initial[1] * k_initial[1] + k_initial[2] * k_initial[2]);
  double dir[3], dirout[3];
  dir[0] = k_initial[0] / k_mag;
  dir[1] = k_initial[1] / k_mag;
  dir[2] = k_initial[2] / k_mag;

  double vsq2ekin = data_transfer.pointer_to_a_NCrystal_physics_storage_struct->ncrystal_convfact_vsq2ekin;
  double ekin = vsq2ekin * K2V * k_mag * K2V * k_mag;
  double ekin_final = 0.0;
  ncrystal_samplescatter (data_transfer.pointer_to_a_NCrystal_physics_storage_struct->stored_params.scat, ekin, (const double (*)[3]) &dir, &ekin_final, &dirout);

  if (ekin_final <= 0)
    return 0;

  double v2 = data_transfer.pointer_to_a_NCrystal_physics_storage_struct->ncrystal_convfact_ekin2vsq * ekin_final;
  double absv = sqrt (v2);
  k_mag = absv * V2K;
  k_final[0] = dirout[0] * k_mag;
  k_final[1] = dirout[1] * k_mag;
  k_final[2] = dirout[2] * k_mag;
  return 1;
}

#ifndef PROCESS_DETECTOR
#define PROCESS_DETECTOR dummy
#endif
#ifndef PROCESS_NCRYSTAL_DETECTOR
#define PROCESS_NCRYSTAL_DETECTOR dummy
#endif

#endif
