import math
import torch
import torch.multiprocessing as mp
import typing as t

from torch.distributed.fsdp import FullyShardedDataParallel as FSDP

from flux.models.integrators import UniformSurveyIntegrator, BaseIntegrator
from flux.models.integrands.base import BaseIntegrand
from flux.models.trainers import BaseTrainer
from flux.utils.constants import IntegrationResult
from flux.utils.pgm import ProcessGroupManager
from flux.models.trainers import VarianceTrainer
from flux.models.flows import BaseFlow, RepeatedCouplingCellFlow
from flux.models.samplers import BaseSampler, UniformSampler
from flux.utils.constants import CellType, MaskingType


def _integrate(
    rank: int,
    world_size: int,
    integrand: BaseIntegrand,
    flow_class: t.Type[BaseFlow],
    trainer_class: t.Type[BaseTrainer],
    integrator_class: t.Type[BaseIntegrator],
    trainer_prior_class: t.Type[BaseSampler],
    cell_type: CellType,
    masking_type: MaskingType,
    n_cells: int,
    n_points: int,
) -> None:
    dim = integrand.dim

    with ProcessGroupManager(rank, world_size, 'nccl'):
        torch.cuda.set_device(rank)

        flow = flow_class(
            dim=dim,
            cell=cell_type,
            masking=masking_type,
            n_cells=n_cells,
        )
        flow = FSDP(
            module=flow.to(rank),
            use_orig_params=True,       #TODO: What is this?
        )

        trainer = trainer_class(
            flow=flow,
            prior=trainer_prior_class(dim=dim, device=rank)
        )

        integrator = integrator_class(
            integrand=integrand,
            trainer=trainer,
            n_points=n_points,
            device=rank,
        )

        result = integrator.integrate()

        print(f'[{rank}] Result: {result.integral:.7e} +- {result.integral_unc:.7e}')
        print(f'[{rank}] Target: {integrand.target:.7e}')


def integrate(
    integrand: BaseIntegrand,
    *,
    flow_class: t.Type[BaseFlow] = RepeatedCouplingCellFlow,
    trainer_class: t.Type[BaseTrainer] = VarianceTrainer,
    integrator_class: t.Type[BaseIntegrator] = UniformSurveyIntegrator,
    trainer_prior_class: t.Type[BaseSampler] = UniformSampler,
    cell_type: CellType = CellType.PWLINEAR,
    masking_type: MaskingType = MaskingType.CHECKERBOARD,
    n_cells: int | None = None,
    n_points: int = 10000,
) -> None:
    world_size = torch.cuda.device_count()
    print(f'Found {world_size} GPUs')

    args = (
        world_size,
        integrand,
        flow_class,
        trainer_class,
        integrator_class,
        trainer_prior_class,
        cell_type,
        masking_type,
        n_cells,
        n_points,
    )
    mp.spawn(
        _integrate,
        args=args,
        nprocs=world_size,
        join=True,
    )


# result = torch.Tensor([result.integral, result.integral_unc])

# results = [torch.zeros_like(result) for _ in range(world_size)] if not rank else None

# dist.gather(
#     tensor=result,
#     gather_list=results,
# )

# if not rank:
#     integrals = np.array([t[0] for t in results])
#     uncertainties = np.array([t[1] for t in results])

#     variance = 1.0 / (1.0 / uncertainties ** 2).sum()
#     integral = (integrals / uncertainties ** 2).sum() * variance
#     integral_unc = variance ** 0.5

#     pipe.send((integral, integral_unc))