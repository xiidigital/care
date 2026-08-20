# Delivery control plane

The `delivery-*.yml` workflows in this directory are **entry points, not
implementations**. They exist on `develop` for one reason: GitHub registers a
`workflow_dispatch` only for a workflow that lives on the repository's default
branch. Before they existed, none of the managed-cloud delivery workflows could
be started by hand, because they live on the `gcp` release lineage:

```
$ gh workflow run build-image.yml --ref feature/ci-controlled-delivery
HTTP 404: workflow build-image.yml not found on the default branch
```

That was ES-08 finding D10.

## The rule this establishes

```
workflow definition location  !=  source revision  !=  deployed image
```

Three separate things, decided separately:

| | decided by |
|---|---|
| where the workflow definition lives | this directory, on `develop` |
| which source revision is built | the `source_ref` input, **verified** |
| what is deployed | an immutable `sha256:` digest |

## What this does not mean

**`develop` is not a deployment target.** Nothing here builds or deploys
`develop`. These files contain no credential, declare no environment, and do no
work — each one calls an implementation pinned to `@gcp` and passes along an
operator's request.

**A feature branch cannot publish or deploy.** `source_ref` is an input, and an
input is attacker-reachable, so it is not trusted. The implementation's first
job is `verify-source.yml`, which resolves the ref and refuses any commit that
is not already reachable from `gcp` — reachability, checked with
`git merge-base --is-ancestor`, not string comparison. Credentialed jobs then
check out the SHA it resolved, never the ref that was typed. Selecting your own
branch gets you a failed run, not a publication identity.

**The environment gates are unchanged.** `production` and
`infrastructure-apply` keep their required reviewers, and the GCP workload
identity bindings accept only the matching environment claim, so a workflow that
does not run in the right GitHub Environment cannot become the identity
regardless of what it asks for.

## Ordering

These shims resolve `@gcp`. The delivery implementation must be merged to `gcp`
before they can run; until then a dispatch fails immediately with "workflow was
not found", which is the correct visible failure rather than a silent one.

## Where the implementation and its rationale live

On the `gcp` lineage: `.github/workflows/` for the workflows,
`docs/xii/adr/ADR-0008-automated-continuous-integration.md` for the decision,
and `docs/xii/architecture/08-continuous-delivery.md` for the operator's guide.
The upstream CARE workflows in this directory are unrelated and untouched.
