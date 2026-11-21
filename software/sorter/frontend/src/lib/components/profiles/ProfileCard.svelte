<script lang="ts">
	import Boxes from '@lucide/svelte/icons/boxes';
	import Ellipsis from '@lucide/svelte/icons/ellipsis';
	import { _ } from 'svelte-i18n';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Card from '$lib/components/ui/Card.svelte';
	import { swatchColor } from '$lib/components/ui/ColorChip.svelte';
	import Menu from '$lib/components/ui/Menu.svelte';
	import PartImage from '$lib/components/ui/PartImage.svelte';
	import Spinner from '$lib/components/ui/Spinner.svelte';
	import {
		displayVersion,
		sourceLabel,
		targetWebUrl,
		visibleVersions
	} from '$lib/sorting-profiles/api';
	import { newerVersion } from '$lib/sorting-profiles/bins';
	import { formatAbsoluteTime, formatRelativeTime } from '$lib/sorting-profiles/format';
	import type {
		HiveTargetLibrary,
		SortingProfileDetail,
		SortingProfileRuleSummary,
		SortingProfileSummary,
		SortingProfileSyncState
	} from '$lib/sorting-profiles/types';

	type Props = {
		target: HiveTargetLibrary;
		profile: SortingProfileSummary;
		detail: SortingProfileDetail | undefined;
		detailError: string | undefined;
		syncState: SortingProfileSyncState | null;
		selectedVersionId: string | null;
		applyingKey: string | null;
		cardKey: string;
		onOpenDetails: () => void;
		onApply: () => void;
		onApplyVersion: (versionId: string) => void;
	};

	const props: Props = $props();

	const isActive = $derived(props.syncState?.profile_id === props.profile.id);

	const isSelectedActive = $derived(
		isActive &&
			Boolean(props.selectedVersionId) &&
			props.syncState?.version_id === props.selectedVersionId
	);

	// The newest version of this profile that Hive has, when it is newer than the
	// one this machine runs (the owner's own newest, anyone else's published).
	const update = $derived.by(() => {
		const sync = props.syncState;
		if (!sync?.profile_id || props.profile.id !== sync.profile_id) return null;
		const latest = newerVersion(props.profile, sync.version_number);
		return latest == null ? null : { latest, current: sync.version_number ?? 0 };
	});

	const rules: SortingProfileRuleSummary[] = $derived(
		(displayVersion(props.profile)?.rules_summary ?? []).filter((rule) => !rule.disabled)
	);

	// What the version sorts into, by its first bins. A version compiled before
	// bins were described has none, and is shown by its rules' names.
	const MAX_BINS = 8;
	const bins = $derived(displayVersion(props.profile)?.bins ?? []);

	const lastUsed = $derived.by(() => {
		if (props.syncState?.profile_id === props.profile.id) {
			return props.syncState.activated_at ?? props.syncState.applied_at ?? null;
		}
		const assignment = props.target.assignment;
		if (assignment?.profile?.id === props.profile.id) {
			return assignment.last_activated_at ?? assignment.last_synced_at ?? null;
		}
		return null;
	});

	const applying = $derived(props.applyingKey === props.cardKey);

	// Choosing a version activates that version (after the confirmation dialog).
	const versionItems = $derived(
		props.detail
			? visibleVersions(props.detail).map((version) => ({
					label: `v${version.version_number}${version.label ? ` - ${version.label}` : ''}`,
					hint: version.is_published
						? undefined
						: $_('components.profiles.profileCard.versionDraftHint'),
					onselect: () => props.onApplyVersion(version.id)
				}))
			: []
	);
</script>

<Card label={props.profile.name} onclick={props.onOpenDetails} class="h-full">
	<div class="flex h-full flex-col gap-3">
		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0 flex-1">
				<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
					<h3 class="truncate text-base font-semibold text-ink">{props.profile.name}</h3>
					{#if isActive}<Badge tone="success" dot
							>{$_('components.profiles.profileCard.activeBadge')}</Badge
						>{/if}
					{#if props.profile.is_default}
						<Badge>{$_('components.profiles.profileCard.hiveDefaultBadge')}</Badge>
					{:else if props.profile.visibility === 'public'}
						<Badge>{$_('components.profiles.profileCard.publicBadge')}</Badge>
					{/if}
				</div>
				{#if update}
					<p class="mt-1 text-sm text-ink-muted">
						{$_('components.profiles.profileCard.updateNotice', {
							values: { latest: update.latest, current: update.current }
						})}
					</p>
				{/if}
			</div>
			<div class="flex shrink-0 items-center gap-1">
				{#if props.detailError}
					<Badge tone="warning">{$_('components.profiles.profileCard.unavailableBadge')}</Badge>
				{:else if props.detail}
					<Button size="sm" loading={applying} onclick={props.onApply}
						>{$_('components.profiles.profileCard.activateButton')}</Button
					>
					<Menu
						label={$_('components.profiles.profileCard.versionsMenuLabel', {
							values: { name: props.profile.name }
						})}
						items={versionItems}
					>
						{#snippet trigger(trigger)}
							<Button
								{...trigger}
								size="sm"
								variant="ghost"
								icon={Ellipsis}
								label={$_('components.profiles.profileCard.chooseVersionLabel')}
								disabled={applying}
							/>
						{/snippet}
					</Menu>
				{:else}
					<span class="flex items-center gap-2 text-sm text-ink-muted">
						<Spinner size={14} />
						{$_('components.profiles.profileCard.loadingLabel')}
					</span>
				{/if}
			</div>
		</div>

		{#if bins.length > 0}
			<ul class="grid grid-cols-1 gap-x-4 gap-y-2 md:grid-cols-2">
				{#each bins.slice(0, MAX_BINS) as bin (bin.id)}
					<li class="flex min-w-0 items-center gap-2 text-sm" title={bin.name ?? undefined}>
						{#if bin.kind === 'fallback' && bin.rgb}
							<span
								aria-hidden="true"
								class="size-6 shrink-0 rounded-check border border-line-strong"
								style:background-color={swatchColor(bin.rgb)}
							></span>
						{:else if bin.kind === 'default'}
							<span
								aria-hidden="true"
								class="flex size-6 shrink-0 items-center justify-center rounded-control bg-hover text-ink-faint"
							>
								<Boxes size={14} />
							</span>
						{:else}
							<PartImage src={bin.image_url} class="size-6 shrink-0" />
						{/if}
						<span class="truncate text-ink">{bin.name}</span>
					</li>
				{/each}
			</ul>
			{#if bins.length > MAX_BINS}
				<div>
					<Button size="sm" variant="ghost" onclick={props.onOpenDetails}
						>{$_('components.profiles.profileCard.seeAllCategories')}</Button
					>
				</div>
			{/if}
		{:else if rules.length > 0}
			<div class="grid grid-cols-1 gap-x-4 gap-y-1.5 md:grid-cols-2">
				{#each rules.slice(0, 8) as rule}
					<div class="truncate text-sm text-ink" title={rule.set_num ?? rule.name}>{rule.name}</div>
				{/each}
				{#if rules.length > 8}
					<div class="md:col-span-2">
						<Button size="sm" variant="ghost" onclick={props.onOpenDetails}>
							{$_('components.profiles.profileCard.moreRules', { values: { count: rules.length - 8 } })}
						</Button>
					</div>
				{/if}
			</div>
		{:else}
			<p class="text-sm text-ink-muted">{$_('components.profiles.profileCard.noRulesDefined')}</p>
		{/if}

		<div class="mt-auto flex flex-col gap-1.5">
			<div class="grid grid-cols-1 items-center gap-x-3 gap-y-1 text-xs text-ink-muted md:grid-cols-[1fr_auto_1fr]">
				<div>
					{#if lastUsed}
						<span title={formatAbsoluteTime(lastUsed) ?? undefined}>
							{$_('components.profiles.profileCard.lastUsed', {
								values: {
									time:
										formatRelativeTime(lastUsed) ??
										$_('components.profiles.profileCard.recentlyFallback')
								}
							})}
						</span>
					{/if}
				</div>
				<div class="md:text-center">
					<span class="font-mono">hive:</span>{#if targetWebUrl(props.target)}<a
							href={targetWebUrl(props.target) ?? undefined}
							target="_blank"
							rel="noreferrer"
							class="text-primary-ink hover:underline">{sourceLabel(props.target)}</a
						>{:else}{sourceLabel(props.target)}{/if}
				</div>
				<div class="md:text-right">
					{#if displayVersion(props.profile)?.created_at}
						<span title={formatAbsoluteTime(displayVersion(props.profile)?.created_at) ?? undefined}>
							{$_('components.profiles.profileCard.updatedLabel', {
								values: {
									time:
										formatRelativeTime(displayVersion(props.profile)?.created_at) ??
										$_('components.profiles.profileCard.recentlyFallback')
								}
							})}
						</span>
					{/if}
				</div>
			</div>
			{#if props.detailError}
				<p class="text-sm text-warning-ink">
					{$_('components.profiles.profileCard.couldNotLoadVersions', {
						values: { error: props.detailError }
					})}
				</p>
			{/if}
			{#if isSelectedActive}
				<p class="text-sm text-ink-muted">{$_('components.profiles.profileCard.currentlyActive')}</p>
			{:else if isActive && props.syncState?.version_number}
				<p class="text-sm text-ink-muted">
					{$_('components.profiles.profileCard.activeOnVersion', {
						values: { version: props.syncState.version_number }
					})}
				</p>
			{/if}
		</div>
	</div>
</Card>
