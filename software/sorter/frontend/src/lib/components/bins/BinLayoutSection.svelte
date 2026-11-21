<script lang="ts">
	import { _ } from 'svelte-i18n';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Badge from '$lib/components/ui/Badge.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import Input from '$lib/components/ui/Input.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import Popover from '$lib/components/ui/Popover.svelte';
	import { requestBackendRestart } from '$lib/backend';
	import { getMachinesContext } from '$lib/machines/context';
	import {
		fetchBinLayouts,
		fetchActiveBinLayout,
		applyBinLayout,
		saveBinLayout,
		createBinLayout,
		renameBinLayout,
		deleteBinLayout,
		type BinLayoutRecord
	} from '$lib/api/bin-layouts';
	import { onMount } from 'svelte';
	import Check from '@lucide/svelte/icons/check';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	let {
		baseUrl,
		profileId = null,
		profileName = ''
	}: { baseUrl: string; profileId?: string | null; profileName?: string } = $props();

	const manager = getMachinesContext();

	let layouts = $state<BinLayoutRecord[]>([]);
	let active = $state<BinLayoutRecord | null>(null);
	let busy = $state(false);
	let restarting = $state(false);
	let error = $state<string | null>(null);
	let status = $state('');
	let dropdownOpen = $state(false);

	let switchOpen = $state(false);
	let saveAsOpen = $state(false);
	let renameOpen = $state(false);
	let deleteOpen = $state(false);
	let target = $state<BinLayoutRecord | null>(null);
	let draftName = $state('');

	const isDirty = $derived(active?.dirty ?? false);

	function profileLabel(id: string | null): string {
		return (id ?? '').replace(/\.json$/, '') || $_('components.bins.binLayoutSection.noProfile');
	}

	async function reload() {
		try {
			const [list, act] = await Promise.all([
				fetchBinLayouts(baseUrl),
				fetchActiveBinLayout(baseUrl)
			]);
			layouts = list.layouts;
			active = act.active;
			error = null;
		} catch (e) {
			error = e instanceof Error ? e.message : $_('components.bins.binLayoutSection.loadError');
		}
	}

	onMount(() => {
		void reload();
		const interval = setInterval(() => void reload(), 2000);
		return () => clearInterval(interval);
	});

	function openSwitch(layout: BinLayoutRecord) {
		dropdownOpen = false;
		if (layout.is_active) return;
		target = layout;
		switchOpen = true;
	}

	function openSaveAs() {
		dropdownOpen = false;
		draftName = '';
		saveAsOpen = true;
	}

	function openRename(layout: BinLayoutRecord) {
		dropdownOpen = false;
		target = layout;
		draftName = layout.name;
		renameOpen = true;
	}

	function openDelete(layout: BinLayoutRecord) {
		dropdownOpen = false;
		target = layout;
		deleteOpen = true;
	}

	async function run(action: () => Promise<void>, successMsg: string) {
		busy = true;
		error = null;
		status = '';
		try {
			await action();
			status = successMsg;
			await reload();
		} catch (e) {
			error = e instanceof Error ? e.message : $_('components.bins.binLayoutSection.somethingWentWrong');
		} finally {
			busy = false;
		}
	}

	async function confirmSwitch() {
		const layout = target;
		switchOpen = false;
		if (!layout) return;
		busy = true;
		error = null;
		status = '';
		try {
			const res = await applyBinLayout(baseUrl, layout.id);
			if (res.restart_required) {
				restarting = true;
				status = $_('components.bins.binLayoutSection.switchingStatus', { values: { name: layout.name } });
				// The new process remounts the page, which loads the new layout.
				const machine = manager.selectedMachine;
				if (await requestBackendRestart(baseUrl)) await manager.waitForNewRun(machine, 90_000);
			}
			await reload();
			status = $_('components.bins.binLayoutSection.switchedStatus', { values: { name: layout.name } });
		} catch (e) {
			error = e instanceof Error ? e.message : $_('components.bins.binLayoutSection.switchError');
		} finally {
			busy = false;
			restarting = false;
		}
	}

	async function confirmSaveAs() {
		const name = draftName.trim();
		saveAsOpen = false;
		if (!name) return;
		await run(
			() => createBinLayout(baseUrl, name).then(() => {}),
			$_('components.bins.binLayoutSection.savedAsStatus', { values: { name } })
		);
	}

	async function confirmRename() {
		const layout = target;
		const name = draftName.trim();
		renameOpen = false;
		if (!layout || !name) return;
		await run(
			() => renameBinLayout(baseUrl, layout.id, name).then(() => {}),
			$_('components.bins.binLayoutSection.renamedStatus', { values: { name } })
		);
	}

	async function confirmDelete() {
		const layout = target;
		deleteOpen = false;
		if (!layout) return;
		await run(
			() => deleteBinLayout(baseUrl, layout.id).then(() => {}),
			$_('components.bins.binLayoutSection.deletedStatus', { values: { name: layout.name } })
		);
	}

	async function saveChanges() {
		const layout = active;
		if (!layout) return;
		await run(
			() => saveBinLayout(baseUrl, layout.id).then(() => {}),
			$_('components.bins.binLayoutSection.savedChangesStatus')
		);
	}
</script>

<Panel
	title={$_('components.bins.binLayoutSection.panelTitle')}
	description={$_('components.bins.binLayoutSection.panelDescription')}
>
	<div class="flex flex-col gap-3">
		<div class="flex flex-wrap items-center gap-2">
			<Popover
				label={$_('components.bins.binLayoutSection.layoutsPopoverLabel')}
				bind:open={dropdownOpen}
				width="22rem"
				padded={false}
			>
				{#snippet trigger(props)}
					<Button {...props} class="w-64 max-w-full" disabled={layouts.length === 0 && !active}>
						<span class="flex w-full min-w-0 items-center justify-between gap-2">
							<span class="flex min-w-0 items-center gap-2">
								<span class="truncate">{active?.name ?? $_('components.bins.binLayoutSection.noLayout')}</span>
								{#if isDirty}<Badge tone="warning">{$_('components.bins.binLayoutSection.unsavedBadge')}</Badge>{/if}
							</span>
							<ChevronDown size={16} class="shrink-0" />
						</span>
					</Button>
				{/snippet}
				<p class="px-3 py-2 text-sm text-ink-muted">
					{$_('components.bins.binLayoutSection.belongsToProfileHelp')}
				</p>
				{#if layouts.length === 0}
					<p class="border-t border-line px-3 py-2 text-sm text-ink-muted">
						{$_('components.bins.binLayoutSection.noSavedLayouts')}
					</p>
				{:else}
					<ul class="divide-y divide-line border-t border-line">
						{#each layouts as layout (layout.id)}
							{@const matches = layout.profile_id === profileId}
							<li class="flex items-center gap-1 py-1 pr-1 pl-1 {matches ? '' : 'opacity-50'}">
								<button
									type="button"
									class="flex min-h-(--size-menu-item) min-w-0 flex-1 items-center gap-2.5 rounded-item px-2 text-left hover:bg-hover disabled:pointer-events-none"
									disabled={busy || layout.is_active || !matches}
									title={matches
										? undefined
										: $_('components.bins.binLayoutSection.switchProfileFirst')}
									onclick={() => openSwitch(layout)}
								>
									<Check
										size={16}
										class="shrink-0 text-success-ink {layout.is_active ? '' : 'invisible'}"
									/>
									<span class="min-w-0">
										<span class="block truncate text-sm font-medium text-ink">{layout.name}</span>
										<span class="block truncate font-mono text-xs text-ink-muted">
											{profileLabel(layout.profile_id)}
										</span>
									</span>
								</button>
								<Button
									size="sm"
									variant="ghost"
									icon={Pencil}
									label={$_('components.bins.binLayoutSection.renameButton', { values: { name: layout.name } })}
									onclick={() => openRename(layout)}
								/>
								<Button
									size="sm"
									variant="ghost"
									icon={Trash2}
									label={layout.is_active
										? $_('components.bins.binLayoutSection.activeCannotBeDeleted')
										: $_('components.bins.binLayoutSection.deleteButton', { values: { name: layout.name } })}
									disabled={layout.is_active}
									onclick={() => openDelete(layout)}
								/>
							</li>
						{/each}
					</ul>
				{/if}
			</Popover>
			<Button disabled={busy} onclick={openSaveAs}>{$_('components.bins.binLayoutSection.saveAsNewButton')}</Button>
			<Button variant="primary" disabled={busy || !isDirty} onclick={() => void saveChanges()}>
				{$_('components.bins.binLayoutSection.saveChangesButton')}
			</Button>
		</div>
		{#if status}<Alert tone="success">{status}</Alert>{/if}
		{#if error}<Alert tone="danger">{error}</Alert>{/if}
	</div>
</Panel>

<Modal bind:open={switchOpen} title={$_('components.bins.binLayoutSection.switchModal.title')} size="sm">
	<p>
		{$_('components.bins.binLayoutSection.switchModal.confirm', { values: { name: target?.name ?? '' } })}
	</p>
	{#snippet footer()}
		<Button variant="ghost" onclick={() => (switchOpen = false)}>
			{$_('components.bins.binLayoutSection.cancelButton')}
		</Button>
		<Button variant="primary" onclick={() => void confirmSwitch()}>
			{$_('components.bins.binLayoutSection.switchModal.switchButton')}
		</Button>
	{/snippet}
</Modal>

<Modal
	open={restarting}
	title={$_('components.bins.binLayoutSection.restartModal.title')}
	size="sm"
	dismissible={false}
	status={$_('components.bins.binLayoutSection.restartModal.status')}
>
	<p>{$_('components.bins.binLayoutSection.restartModal.body')}</p>
</Modal>

<Modal bind:open={saveAsOpen} title={$_('components.bins.binLayoutSection.saveAsModal.title')} size="sm">
	<Input
		aria-label={$_('components.bins.binLayoutSection.layoutNameLabel')}
		placeholder={$_('components.bins.binLayoutSection.layoutNameLabel')}
		bind:value={draftName}
	/>
	{#snippet footer()}
		<Button variant="ghost" onclick={() => (saveAsOpen = false)}>
			{$_('components.bins.binLayoutSection.cancelButton')}
		</Button>
		<Button variant="primary" disabled={!draftName.trim()} onclick={() => void confirmSaveAs()}
			>{$_('components.bins.binLayoutSection.saveAsModal.saveButton')}</Button
		>
	{/snippet}
</Modal>

<Modal bind:open={renameOpen} title={$_('components.bins.binLayoutSection.renameModal.title')} size="sm">
	<Input
		aria-label={$_('components.bins.binLayoutSection.layoutNameLabel')}
		placeholder={$_('components.bins.binLayoutSection.layoutNameLabel')}
		bind:value={draftName}
	/>
	{#snippet footer()}
		<Button variant="ghost" onclick={() => (renameOpen = false)}>
			{$_('components.bins.binLayoutSection.cancelButton')}
		</Button>
		<Button variant="primary" disabled={!draftName.trim()} onclick={() => void confirmRename()}
			>{$_('components.bins.binLayoutSection.renameModal.renameButton')}</Button
		>
	{/snippet}
</Modal>

<Modal bind:open={deleteOpen} title={$_('components.bins.binLayoutSection.deleteModal.title')} size="sm">
	<p>
		{$_('components.bins.binLayoutSection.deleteModal.confirm', { values: { name: target?.name ?? '' } })}
	</p>
	{#snippet footer()}
		<Button variant="ghost" onclick={() => (deleteOpen = false)}>
			{$_('components.bins.binLayoutSection.cancelButton')}
		</Button>
		<Button variant="danger" onclick={() => void confirmDelete()}>
			{$_('components.bins.binLayoutSection.deleteModal.deleteButton')}
		</Button>
	{/snippet}
</Modal>
