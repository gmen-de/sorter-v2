<script lang="ts">
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import AppShell from '$lib/components/AppShell.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import MediaTile from '$lib/components/ui/MediaTile.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import { getMachinesContext } from '$lib/machines/context';
	import { _ } from 'svelte-i18n';

	const manager = getMachinesContext();

	const channels = $derived([
		{ id: 2, label: $_('routes.perceptionDebug.channelLabel2') },
		{ id: 3, label: $_('routes.perceptionDebug.channelLabel3') },
		{ id: 4, label: $_('routes.perceptionDebug.channelLabel4') }
	]);

	function baseUrl(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	// Cache-busting token. Bumped on mount (so a browser refresh always pulls the
	// current frame) and by the Refresh button (on-demand re-fetch, no reload).
	let token = $state(Date.now());
	let base = $derived(baseUrl());
	let failed = $state<Record<number, boolean>>({});

	// Which inference to view per channel:
	//  - 'cropped'   : production — model runs on the polygon-bounding-rect crop.
	//  - 'fullframe' : debug — same model on the whole frame (a 2nd inference per
	//                  cycle, enabled on demand on the backend, self-expiring).
	let mode = $state<'cropped' | 'fullframe'>('cropped');
	const endpoint = $derived(mode === 'fullframe' ? 'fullframe' : 'annotated');

	// In full-frame mode the first fetch can 425 ("warming up") for one cycle
	// while the worker produces the first uncropped result; auto-refresh shortly
	// after so the image appears without the user clicking.
	let timer: ReturnType<typeof setTimeout> | undefined;
	function srcFor(id: number): string {
		return `${base}/api/perception/debug/${endpoint}/${id}?t=${token}`;
	}

	function refresh(): void {
		failed = {};
		token = Date.now();
	}

	function setMode(next: 'cropped' | 'fullframe'): void {
		mode = next;
		refresh();
		clearTimeout(timer);
		if (next === 'fullframe') {
			// Pull again after the worker has had a cycle to run the full-frame pass.
			timer = setTimeout(refresh, 800);
		}
	}
</script>

<svelte:head>
	<title>{$_('routes.perceptionDebug.pageTitle')}</title>
</svelte:head>

<AppShell>
	<div class="mx-auto flex w-full max-w-[1600px] flex-col gap-(--gap-panels) px-4 py-6 sm:px-6">
		<PageHeader
			title={$_('routes.perceptionDebug.title')}
			description={$_('routes.perceptionDebug.description')}
		/>

		<Panel>
			<div class="flex flex-col gap-4">
				<div class="flex flex-wrap items-center gap-2">
					<SegmentedControl
						label={$_('routes.perceptionDebug.modeLabel')}
						value={mode}
						options={[
							{ value: 'cropped', label: $_('routes.perceptionDebug.modeCropped') },
							{ value: 'fullframe', label: $_('routes.perceptionDebug.modeFullframe') }
						]}
						onchange={setMode}
					/>
					<Button variant="primary" onclick={refresh}>{$_('routes.perceptionDebug.refreshButton')}</Button>
				</div>
				{#if mode === 'cropped'}
					<p class="max-w-4xl text-sm text-ink-muted">
						{$_('routes.perceptionDebug.croppedDescription')}
					</p>
				{:else}
					<p class="max-w-4xl text-sm text-ink-muted">
						{$_('routes.perceptionDebug.fullframeDescription')}
					</p>
				{/if}
			</div>
		</Panel>

		<div class="grid grid-cols-1 gap-(--gap-panels) xl:grid-cols-2">
			{#each channels as channel (channel.id)}
				<MediaTile title={channel.label} expandable>
					{#if failed[channel.id]}
						<p class="px-6 text-center text-sm text-ink-muted">
							{$_('routes.perceptionDebug.noFrameAvailable')}
						</p>
					{:else}
						<img
							class="absolute inset-0 h-full w-full object-contain"
							src={srcFor(channel.id)}
							alt={$_('routes.perceptionDebug.annotatedFrameAlt', { values: { channel: channel.label } })}
							onerror={() => (failed = { ...failed, [channel.id]: true })}
						/>
					{/if}
				</MediaTile>
			{/each}
		</div>
	</div>
</AppShell>
