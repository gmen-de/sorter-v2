<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Check from '@lucide/svelte/icons/check';
	import Circle from '@lucide/svelte/icons/circle';
	import CameraFeed from '$lib/components/CameraFeed.svelte';
	import CameraSourcePreview from '$lib/components/CameraSourcePreview.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import type { CameraRole } from '$lib/settings/stations';

	let changingCamera = $state(false);

	type CameraChoice = {
		key: string;
		label: string;
		source: number | string | null;
	};

	let {
		role,
		label,
		description,
		required = false,
		selectedKey = '__none__',
		selectedLabel = $_('components.setup.setupCameraAreaCard.defaultSelectedLabel'),
		choices = [],
		onSelect,
		zoneReviewed = false,
		pictureTuned = false,
		onOpenPictureSettings,
		onOpenZoneEditor
	}: {
		role: CameraRole;
		label: string;
		description: string;
		required?: boolean;
		selectedKey?: string;
		selectedLabel?: string;
		choices?: CameraChoice[];
		zoneReviewed?: boolean;
		pictureTuned?: boolean;
		onSelect?: (role: string, key: string) => void;
		onOpenPictureSettings?: (role: string) => void;
		onOpenZoneEditor?: (role: string) => void;
	} = $props();

	const selectedSource = $derived(
		selectedKey === '__none__'
			? null
			: (choices.find((choice) => choice.key === selectedKey)?.source ?? null)
	);
	const others = $derived(choices.filter((choice) => choice.key !== '__none__'));
</script>

{#snippet choice(option: CameraChoice, onpick: () => void, chosen = false, onWell = false)}
	<button
		type="button"
		onclick={onpick}
		aria-pressed={chosen}
		class="flex min-h-0 w-full flex-col overflow-hidden rounded-control text-left transition-colors {chosen
			? 'bg-primary-soft'
			: onWell
				? 'bg-surface hover:bg-hover'
				: 'bg-well hover:bg-hover'}"
	>
		<span class="dark relative block min-h-0 flex-1 bg-media">
			<CameraSourcePreview source={option.source} label={option.label} />
		</span>
		<span
			class="flex items-center gap-1.5 px-2.5 py-1.5 text-sm {chosen ? 'text-primary-ink' : 'text-ink'}"
		>
			{#if chosen}<Check size={14} class="shrink-0" />{/if}
			<span class="truncate">{option.label}</span>
		</span>
	</button>
{/snippet}

{#snippet need(done: boolean, text: string, action: string, onclick: () => void)}
	<li class="flex items-center gap-3 px-(--pad-panel) py-2">
		<span class="shrink-0 {done ? 'text-success-ink' : 'text-ink-faint'}">
			{#if done}<Check size={16} />{:else}<Circle size={16} />{/if}
		</span>
		<span class="min-w-0 flex-1 truncate text-sm text-ink">{text}</span>
		<Button size="sm" variant="ghost" disabled={selectedSource === null} {onclick}>{action}</Button>
	</li>
{/snippet}

<!-- One camera role: its picture, and the three things it needs. -->
<Panel title={label} {description} flush>
	{#snippet actions()}<Badge>{required ? $_('components.setup.setupCameraAreaCard.badge.required') : $_('components.setup.setupCameraAreaCard.badge.optional')}</Badge>{/snippet}
	{#if selectedSource !== null}
		<CameraFeed camera={role} label={selectedLabel} header={false} />
	{:else if others.length > 0}
		<div class="grid aspect-video grid-cols-2 gap-2 bg-well p-2">
			{#each others as option (option.key)}
				{@render choice(option, () => onSelect?.(role, option.key), false, true)}
			{/each}
		</div>
	{:else}
		<div class="flex aspect-video flex-col items-center justify-center gap-1 bg-well px-6 text-center text-sm">
			<span class="font-medium text-ink">{$_('components.setup.setupCameraAreaCard.empty.title')}</span>
			<span class="text-ink-muted">{$_('components.setup.setupCameraAreaCard.empty.description')}</span>
		</div>
	{/if}
	<ul class="divide-y divide-line">
		{@render need(
			selectedSource !== null,
			selectedSource !== null
				? $_('components.setup.setupCameraAreaCard.status.cameraChosen')
				: $_('components.setup.setupCameraAreaCard.status.noCameraChosen'),
			$_('components.setup.setupCameraAreaCard.actions.change'),
			() => (changingCamera = true)
		)}
		{@render need(
			zoneReviewed,
			zoneReviewed
				? $_('components.setup.setupCameraAreaCard.status.zoneReviewed')
				: $_('components.setup.setupCameraAreaCard.status.zoneNotReviewed'),
			$_('components.setup.setupCameraAreaCard.actions.review'),
			() => onOpenZoneEditor?.(role)
		)}
		{@render need(
			pictureTuned,
			pictureTuned
				? $_('components.setup.setupCameraAreaCard.status.pictureTuned')
				: $_('components.setup.setupCameraAreaCard.status.pictureNotTuned'),
			$_('components.setup.setupCameraAreaCard.actions.tune'),
			() => onOpenPictureSettings?.(role)
		)}
	</ul>
</Panel>

<Modal
	open={changingCamera && selectedSource !== null}
	title={$_('components.setup.setupCameraAreaCard.modal.title')}
	size="lg"
	onclose={() => (changingCamera = false)}
>
	<p class="mb-4 text-ink-muted">
		{$_('components.setup.setupCameraAreaCard.modal.pickSource', { values: { label } })}
	</p>
	<div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
		{#each others as option (option.key)}
			<div class="aspect-4/3 flex">
				{@render choice(
					option,
					() => {
						onSelect?.(role, option.key);
						changingCamera = false;
					},
					option.key === selectedKey
				)}
			</div>
		{/each}
	</div>
	{#snippet footer()}
		<Button
			variant="danger"
			class="mr-auto"
			onclick={() => {
				onSelect?.(role, '__none__');
				changingCamera = false;
			}}
		>
			{$_('components.setup.setupCameraAreaCard.modal.clear')}
		</Button>
		<Button variant="ghost" onclick={() => (changingCamera = false)}
			>{$_('components.setup.setupCameraAreaCard.modal.close')}</Button
		>
	{/snippet}
</Modal>
