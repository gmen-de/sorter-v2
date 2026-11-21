<script lang="ts">
	// The profile this machine sorts with now: which one and which version, what
	// it sorts into, and a notice when Hive has a newer version of it.
	import Boxes from '@lucide/svelte/icons/boxes';
	import { _ } from 'svelte-i18n';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import { formatRelativeTime } from '$lib/sorting-profiles/format';
	import type { LocalProfileStatus, SortingProfileSyncState } from '$lib/sorting-profiles/types';
	import type { SortingProfileMetadata } from '$lib/stores/sortingProfile.svelte';

	type Props = {
		syncState: SortingProfileSyncState | null;
		localProfile: LocalProfileStatus | null;
		metadata: SortingProfileMetadata | null;
		// A newer version of this profile on Hive than the machine runs.
		update: { latest: number; current: number } | null;
		updating: boolean;
		onUpdate: () => void;
		onOpenBins: () => void;
	};

	let { syncState, localProfile, metadata, update, updating, onUpdate, onOpenBins }: Props =
		$props();

	const name = $derived(syncState?.profile_name ?? localProfile?.name ?? metadata?.name ?? null);
	const from = $derived.by(() => {
		if (syncState?.source === 'local') {
			return $_('components.profiles.activeProfilePanel.savedAsFile', {
				values: {
					filename:
						syncState.local_filename ?? $_('components.profiles.activeProfilePanel.aFile')
				}
			});
		}
		const target = syncState?.target_name;
		if (!target) return null;
		return /hive/i.test(target)
			? $_('components.profiles.activeProfilePanel.fromTarget', { values: { target } })
			: $_('components.profiles.activeProfilePanel.fromHiveTarget', { values: { target } });
	});
	const applied = $derived(
		formatRelativeTime(syncState?.applied_at ?? localProfile?.updated_at ?? null)
	);
	// What it sorts into, in a sentence: "50 categories; 59,000 of 81,000 known parts are sorted."
	const sorts = $derived.by(() => {
		if (!metadata) return null;
		const count = Object.keys(metadata.categories ?? {}).length;
		const categoryWord =
			count === 1
				? $_('components.profiles.activeProfilePanel.category')
				: $_('components.profiles.activeProfilePanel.categories');
		const bins = `${whole(count)} ${categoryWord}`;
		const { sorted, total_parts: total } = metadata.stats ?? {};
		if (sorted == null || total == null) return `${bins}.`;
		return sorted === total
			? $_('components.profiles.activeProfilePanel.allSorted', {
					values: { bins, total: whole(total) }
				})
			: $_('components.profiles.activeProfilePanel.partialSorted', {
					values: { bins, sorted: whole(sorted), total: whole(total) }
				});
	});

	function whole(n: number) {
		return n.toLocaleString('en-US');
	}
</script>

<Panel
	title={$_('components.profiles.activeProfilePanel.panelTitle')}
	description={$_('components.profiles.activeProfilePanel.panelDescription')}
>
	{#snippet actions()}
		<Button size="sm" icon={Boxes} onclick={onOpenBins}
			>{$_('components.profiles.activeProfilePanel.seeCategories')}</Button
		>
	{/snippet}
	<div class="flex flex-col gap-3">
		<div>
			<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
				<span class="text-base font-semibold text-ink"
					>{name ?? $_('components.profiles.activeProfilePanel.noName')}</span
				>
				{#if syncState?.version_number}
					<Badge
						>v{syncState.version_number}{syncState.version_label
							? ` · ${syncState.version_label}`
							: ''}</Badge
					>
				{/if}
			</div>
			<p class="mt-0.5 text-sm text-ink-muted">
				{[
					from,
					applied &&
						$_('components.profiles.activeProfilePanel.appliedAt', { values: { applied } })
				]
					.filter(Boolean)
					.join(', ')}
			</p>
			{#if sorts}<p class="mt-0.5 text-sm text-ink-muted">{sorts}</p>{/if}
		</div>

		{#if update}
			<Alert
				tone="info"
				title={$_('components.profiles.activeProfilePanel.updateAlertTitle', {
					values: { latest: update.latest, current: update.current }
				})}
			>
				{$_('components.profiles.activeProfilePanel.updateAlertBody')}
				{#snippet actions()}
					<Button variant="primary" size="sm" loading={updating} onclick={onUpdate}
						>{$_('components.profiles.activeProfilePanel.updateButton')}</Button
					>
				{/snippet}
			</Alert>
		{/if}
	</div>
</Panel>
