<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import Alert from '$lib/components/ui/Alert.svelte';
	import ProgressBar from '$lib/components/ui/ProgressBar.svelte';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import { getMachineContext } from '$lib/machines/context';

	type ReleaseAsset = {
		name: string;
		size: number | null;
		download_url: string;
		family: string;
		variant: string;
		role: string;
	};
	type Release = {
		tag: string;
		version: string;
		published_at: string | null;
		prerelease: boolean;
		assets: ReleaseAsset[];
	};
	type FlashJob = {
		job_id: string;
		phase: string;
		progress: number | null;
		status: 'running' | 'done' | 'failed' | 'cancelled';
		error: string | null;
		log: string[];
		asset_name: string | null;
	};

	const machine = getMachineContext();

	let loading = $state(true);
	let loadError = $state<string | null>(null);
	let releases = $state<Release[]>([]);

	let job = $state<FlashJob | null>(null);
	let jobPollTimer: ReturnType<typeof setInterval> | null = null;
	let starting = $state(false);
	let startError = $state<string | null>(null);

	const PHASE_LABELS: Record<string, string> = {
		queued: 'Queued',
		downloading: 'Downloading firmware',
		waiting_port: 'Waiting for the ESP32 on USB',
		flashing: 'Flashing via esptool',
		done: 'Done'
	};

	function backendBase(): string {
		return machineHttpBaseUrlFromWsUrl(machine.machine?.url) ?? getBackendHttpBase();
	}

	function esp32Asset(release: Release): ReleaseAsset | null {
		return release.assets.find((a) => a.family === 'esp32_ble_bridge') ?? null;
	}

	async function loadReleases() {
		loading = true;
		loadError = null;
		try {
			const res = await fetch(`${backendBase()}/api/firmware/releases`);
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			releases = ((data.releases ?? []) as Release[]).filter((r) => esp32Asset(r) !== null);
		} catch (e: any) {
			loadError = e?.message ?? 'Failed to reach GitHub releases.';
		} finally {
			loading = false;
		}
	}

	async function flash(release: Release) {
		const asset = esp32Asset(release);
		if (!asset) return;
		starting = true;
		startError = null;
		try {
			const res = await fetch(`${backendBase()}/api/hardware-config/waveshare-ble/esp32-flash`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ asset_url: asset.download_url, asset_name: asset.name })
			});
			if (!res.ok) throw new Error(await res.text());
			const data = await res.json();
			job = data.job;
			startJobPolling(data.job_id);
		} catch (e: any) {
			startError = e?.message ?? 'Could not start the flash.';
		} finally {
			starting = false;
		}
	}

	function startJobPolling(jobId: string) {
		stopJobPolling();
		jobPollTimer = setInterval(async () => {
			try {
				const res = await fetch(`${backendBase()}/api/hardware-config/waveshare-ble/esp32-flash/${jobId}`);
				if (!res.ok) return;
				job = await res.json();
				if (job && job.status !== 'running') stopJobPolling();
			} catch {
				/* transient; keep polling */
			}
		}, 1000);
	}

	function stopJobPolling() {
		if (jobPollTimer) clearInterval(jobPollTimer);
		jobPollTimer = null;
	}

	async function cancel() {
		if (!job) return;
		await fetch(`${backendBase()}/api/hardware-config/waveshare-ble/esp32-flash/${job.job_id}/cancel`, {
			method: 'POST'
		});
	}

	onMount(() => void loadReleases());
	onDestroy(() => stopJobPolling());
</script>

<Panel
	title="ESP32 firmware"
	description="Flash the esp32_ble_servo_bridge firmware from a GitHub release, over USB-serial."
	flush
>
	<div class="flex flex-col gap-4 px-(--pad-panel) pb-(--pad-panel)">
		<Alert tone="warning">
			Connect the ESP32 over USB-C before flashing. The moment it is seen on USB, the chute
			stepper locks for safety until the backend restarts — this is intentional, so a reset
			caused by flashing (the ESP32's or, afterward, the Octopus board's) can never move the
			chute unsupervised.
		</Alert>

		{#if loadError}
			<Alert tone="danger">{loadError}</Alert>
		{:else if loading}
			<div class="flex items-center gap-2 text-sm text-ink-muted"><Spinner size={14} /> Loading releases</div>
		{:else if releases.length === 0}
			<p class="text-sm text-ink-muted">No ESP32 firmware releases found.</p>
		{:else}
			{#each releases.slice(0, 5) as release (release.tag)}
				{@const asset = esp32Asset(release)}
				<div class="flex items-center justify-between gap-3 rounded border border-stroke-muted p-2">
					<div class="flex flex-col">
						<span class="text-sm font-medium text-ink">{release.version}</span>
						<span class="text-sm text-ink-muted">{asset?.name}</span>
					</div>
					<Button
						variant="primary"
						disabled={starting || job?.status === 'running'}
						onclick={() => void flash(release)}
					>
						Flash
					</Button>
				</div>
			{/each}
		{/if}

		{#if startError}
			<Alert tone="danger">{startError}</Alert>
		{/if}

		{#if job}
			<div class="flex flex-col gap-2 rounded border border-stroke-muted p-3">
				<span class="text-sm font-medium text-ink">{PHASE_LABELS[job.phase] ?? job.phase}</span>
				{#if job.status === 'running' && job.progress !== null}
					<ProgressBar label="Flashing" value={Math.round(job.progress * 100)} />
				{/if}
				{#if job.status === 'running'}
					<div>
						<Button variant="secondary" onclick={() => void cancel()}>Cancel</Button>
					</div>
				{/if}
				{#if job.status === 'done'}
					<Alert tone="success">Flash complete.</Alert>
				{:else if job.status === 'failed'}
					<Alert tone="danger">{job.error ?? 'Flash failed.'}</Alert>
				{:else if job.status === 'cancelled'}
					<Alert tone="warning">Cancelled.</Alert>
				{/if}
			</div>
		{/if}
	</div>
</Panel>
