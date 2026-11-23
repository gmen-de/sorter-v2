<script lang="ts">
	import { confirmDialog } from '$lib/confirm.svelte';
	import { _ } from 'svelte-i18n';
	import { getBackendHttpBase, machineHttpBaseUrlFromWsUrl } from '$lib/backend';
	import AppShell from '$lib/components/AppShell.svelte';
	import BinDetailsModal from '$lib/components/bins/BinDetailsModal.svelte';
	import BinLayoutSection from '$lib/components/bins/BinLayoutSection.svelte';
	import BinsHeaderActions from '$lib/components/bins/BinsHeaderActions.svelte';
	import BinSearchBar from '$lib/components/bins/BinSearchBar.svelte';
	import ColumnsPanel from '$lib/components/bins/ColumnsPanel.svelte';
	import DiscardBinCard from '$lib/components/bins/DiscardBinCard.svelte';
	import LayerPanel from '$lib/components/bins/LayerPanel.svelte';
	import { categoryLabel } from '$lib/components/bins/pieces';
	import SnapshotsModal from '$lib/components/bins/SnapshotsModal.svelte';
	import type { BinContents, BinInfo, ExitBin, LayerInfo, SetMeta, SetProgressSummary } from '$lib/components/bins/types';
	import Alert from '$lib/components/ui/Alert.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import EmptyState from '$lib/components/ui/EmptyState.svelte';
	import Modal from '$lib/components/ui/Modal.svelte';
	import PageHeader from '$lib/components/ui/PageHeader.svelte';
	import Panel from '$lib/components/ui/Panel.svelte';
	import SettingRow from '$lib/components/ui/SettingRow.svelte';
	import Skeleton from '$lib/components/ui/Skeleton.svelte';
	import Switch from '$lib/components/ui/Switch.svelte';
	import { getMachinesContext } from '$lib/machines/context';
	import { sortingProfileStore } from '$lib/stores/sortingProfile.svelte';
	import { onMount } from 'svelte';

	const manager = getMachinesContext();
	// Active profile (id/name) — bin layouts are scoped to it. Local profiles carry a
	// local_filename rather than a profile_id; either identifies the profile a layout ties to.
	const profileSync = $derived(
		(manager.selectedMachine?.sortingProfileStatus as { sync_state?: Record<string, unknown> } | null)
			?.sync_state ?? null
	);
	const activeProfileId = $derived(
		(profileSync?.profile_id as string | undefined) ??
			(profileSync?.local_filename as string | undefined) ??
			null
	);
	const activeProfileName = $derived((profileSync?.profile_name as string | undefined) ?? '');
	let activeBaseUrl = $state(baseUrl());

	type ClearingState = {
		endpoint: 'contents/clear' | 'categories/clear';
		scope: 'all' | 'layer' | 'bin';
		busyKey: string;
		layerIndex?: number;
		sectionIndex?: number;
		binIndex?: number;
	};

	let layers = $state<LayerInfo[]>([]);
	let currentAngle = $state<number | null>(null);
	let activeLayer = $state<number | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);
	let movingTo = $state<string | null>(null);
	let homing = $state(false);
	let statusMsg = $state('');
	let clearingStates = $state<ClearingState[]>([]);
	let togglingLayerKey = $state<number | null>(null);
	let sectionBusyKey = $state<string | null>(null);
	let pointingSectionKey = $state<string | null>(null);
	let allowMultiCategory = $state(false);
	let savingMultiCategory = $state(false);
	let autoAssignBusy = $state(false);
	let autoAssignResult = $state<string | null>(null);
	let contentsByKey = $state<Record<string, BinContents>>({});
	let detailsOpen = $state(false);
	let detailsBin = $state<{ bin: BinInfo; layerIndex: number; contents: BinContents | null } | null>(null);
	let snapshotsOpen = $state(false);
	let niiBusyLayer = $state<number | null>(null);
	let exitBin = $state<ExitBin | null>(null);
	let savingExitBin = $state(false);
	let setProgressByCategoryId = $state<Record<string, SetProgressSummary>>({});
	let searchQuery = $state('');

	const searchActive = $derived(searchQuery.trim().length > 0);

	// Client-side "find a part": matches bin number, assigned category label, and
	// the bin's grouped contents (part id, BrickLink part name, color name).
	function binMatchesSearch(layerIndex: number, bin: BinInfo): boolean {
		const query = searchQuery.trim().toLowerCase();
		if (!query) return true;
		if (String(bin.global_index + 1) === query) return true;
		if (categoryLabel(bin.category_ids).toLowerCase().includes(query)) return true;
		const contents = contentsForBin(layerIndex, bin);
		if (!contents) return false;
		for (const item of contents.items) {
			if (item.part_id && item.part_id.toLowerCase().includes(query)) return true;
			if (item.color_name && item.color_name.toLowerCase().includes(query)) return true;
		}
		return false;
	}

	const totalBinCount = $derived(layers.reduce((sum, layer) => sum + layer.bins.length, 0));

	const searchMatchCount = $derived.by((): number | null => {
		if (!searchActive) return null;
		let count = 0;
		for (const layer of layers) {
			for (const bin of layer.bins) {
				if (binMatchesSearch(layer.layer_index, bin)) count += 1;
			}
		}
		return count;
	});

	function baseUrl(): string {
		return (
			machineHttpBaseUrlFromWsUrl(
				manager.selectedMachine?.status === 'connected' ? manager.selectedMachine.url : null
			) ?? getBackendHttpBase()
		);
	}

	function binKey(layerIndex: number, sectionIndex: number, binIndex: number): string {
		return `${layerIndex}:${sectionIndex}:${binIndex}`;
	}

	function currentContentsCsvUrl(): string {
		return `${baseUrl()}/api/bins/contents/export.csv`;
	}

	async function loadLayout() {
		try {
			const res = await fetch(`${baseUrl()}/api/bins/layout`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const data = await res.json();
			layers = data.layers ?? [];
			currentAngle = data.current_angle;
			activeLayer = data.active_layer;
			exitBin = data.exit_bin ?? null;
			error = null;
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.loadLayoutFailedFallback');
		} finally {
			loading = false;
		}
	}

	async function loadBinSettings() {
		try {
			const res = await fetch(`${baseUrl()}/api/bins/settings`);
			if (!res.ok) return;
			const data = await res.json();
			allowMultiCategory = Boolean(data.allow_multiple_categories_per_bin);
		} catch {
			// Keep last known setting on transient failures.
		}
	}

	async function setLayerNotInInventory(layerIndex: number, enabled: boolean) {
		niiBusyLayer = layerIndex;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/not-in-inventory/set`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ scope: 'layer', layer_index: layerIndex, enabled })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			await loadLayout();
			statusMsg = enabled
				? $_('routes.bins.layerNiiEnabledMsg', { values: { n: layerIndex + 1 } })
				: $_('routes.bins.layerNiiDisabledMsg', { values: { n: layerIndex + 1 } });
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.updateNiiFailedFallback');
		} finally {
			niiBusyLayer = null;
		}
	}

	async function setBinNotInInventory(layerIndex: number, bin: BinInfo, enabled: boolean) {
		niiBusyLayer = layerIndex;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/not-in-inventory/set`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					scope: 'bin',
					layer_index: layerIndex,
					section_index: bin.section_index,
					bin_index: bin.bin_index,
					enabled
				})
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			await loadLayout();
			statusMsg = enabled
				? $_('routes.bins.binNiiEnabledMsg', { values: { bin: bin.global_index + 1, layer: layerIndex + 1 } })
				: $_('routes.bins.binNiiDisabledMsg', { values: { bin: bin.global_index + 1, layer: layerIndex + 1 } });
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.updateNiiFailedFallback');
		} finally {
			niiBusyLayer = null;
		}
	}

	async function setExitBin(layerIndex: number, sectionIndex: number, binIndex: number) {
		if (savingExitBin) return;
		savingExitBin = true;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/exit/set`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ layer_index: layerIndex, section_index: sectionIndex, bin_index: binIndex })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			exitBin = data.exit_bin ?? null;
			statusMsg = $_('routes.bins.exitBinSetMsg', {
				values: { layer: layerIndex + 1, section: sectionIndex + 1, bin: binIndex + 1 }
			});
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.exitBinSetFailedFallback');
		} finally {
			savingExitBin = false;
		}
	}

	async function clearExitBin() {
		if (savingExitBin) return;
		savingExitBin = true;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/exit/set`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ layer_index: null })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			exitBin = null;
			statusMsg = $_('routes.bins.exitBinClearedMsg');
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.exitBinSetFailedFallback');
		} finally {
			savingExitBin = false;
		}
	}

	async function runAutoAssign(overlap: boolean) {
		if (autoAssignBusy) return;
		autoAssignBusy = true;
		autoAssignResult = null;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/auto-assign`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ overlap })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			await loadLayout();
			if (data.assigned === 0) {
				autoAssignResult = data.message ?? $_('routes.bins.autoAssignNothingFallback');
			} else {
				const binWord =
					data.bins_used === 1
						? $_('routes.bins.binWordSingular')
						: $_('routes.bins.binWordPlural');
				const overlapSuffix = overlap ? ` ${$_('routes.bins.overlapSuffix')}` : '';
				autoAssignResult = $_('routes.bins.autoAssignResult', {
					values: {
						categories: data.categories_ranked,
						bins: data.bins_used,
						binWord,
						pieces: data.total_pieces,
						overlapSuffix
					}
				});
			}
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.autoAssignFailedFallback');
		} finally {
			autoAssignBusy = false;
		}
	}

	async function toggleMultiCategory() {
		if (savingMultiCategory) return;
		savingMultiCategory = true;
		statusMsg = '';
		error = null;
		const next = !allowMultiCategory;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/settings`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ allow_multiple_categories_per_bin: next })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			allowMultiCategory = Boolean(data.allow_multiple_categories_per_bin);
			statusMsg = allowMultiCategory
				? $_('routes.bins.multiCategoryEnabledMsg')
				: $_('routes.bins.multiCategorySingleMsg');
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.updateSettingFailedFallback');
		} finally {
			savingMultiCategory = false;
		}
	}

	async function loadSetProgress() {
		try {
			const res = await fetch(`${baseUrl()}/api/set-progress`);
			if (!res.ok) return;
			const data = await res.json();
			const sets = Array.isArray(data?.progress?.sets) ? data.progress.sets : [];
			const next: Record<string, SetProgressSummary> = {};
			for (const entry of sets) {
				if (!entry || typeof entry.id !== 'string') continue;
				next[entry.id] = {
					total_needed: Number(entry.total_needed) || 0,
					total_found: Number(entry.total_found) || 0,
					pct: Number(entry.pct) || 0
				};
			}
			setProgressByCategoryId = next;
		} catch {
			// Keep last known progress on transient failures.
		}
	}

	function binSetProgress(categoryIds: string[]): SetProgressSummary | null {
		if (!categoryIds || categoryIds.length !== 1) return null;
		return setProgressByCategoryId[categoryIds[0]] ?? null;
	}

	function applyBinsContents(bins: unknown) {
		const next: Record<string, BinContents> = {};
		for (const entry of Array.isArray(bins) ? bins : []) {
			if (!entry || typeof entry !== 'object' || typeof entry.bin_key !== 'string') continue;
			next[entry.bin_key] = entry as BinContents;
		}
		contentsByKey = next;
	}

	// Change token from /api/bins/contents/version. The heavy contents payload is
	// only fetched when this changes; direct loads reset it to null so the next
	// version tick re-syncs after mutations.
	let contentsVersion: string | null = null;
	let contentsLoaded = $state(false);

	async function loadBinContents() {
		contentsVersion = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/contents`);
			if (!res.ok) throw new Error(`HTTP ${res.status}`);
			const data = await res.json();
			applyBinsContents(data.bins);
			contentsLoaded = true;
		} catch {
			// Keep last known contents on transient failures.
		}
	}

	async function pollBinContentsVersion() {
		try {
			const res = await fetch(`${baseUrl()}/api/bins/contents/version`);
			if (!res.ok) return;
			const data = await res.json();
			const version = typeof data.version === 'string' ? data.version : null;
			if (version === null || version === contentsVersion) return;
			await loadBinContents();
			contentsVersion = version;
		} catch {
			// Retry on the next tick.
		}
	}

	function contentsForBin(layerIndex: number, bin: BinInfo): BinContents | null {
		return contentsByKey[binKey(layerIndex, bin.section_index, bin.bin_index)] ?? null;
	}

	async function moveToBin(layerIndex: number, sectionIndex: number, binIndex: number) {
		const key = `${layerIndex}-${sectionIndex}-${binIndex}`;
		if (movingTo) return;
		movingTo = key;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/move-to`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					layer_index: layerIndex,
					section_index: sectionIndex,
					bin_index: binIndex
				})
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			statusMsg = $_('routes.bins.movedToAngleMsg', { values: { angle: data.target_angle } });
			activeLayer = layerIndex;
			currentAngle = data.target_angle;
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.moveFailedFallback');
		} finally {
			movingTo = null;
		}
	}

	async function homeChute() {
		if (homing || movingTo || hasAnyClearing() || togglingLayerKey !== null) return;
		homing = true;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/hardware-config/chute/calibrate/find-endstop`, {
				method: 'POST'
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			statusMsg = $_('routes.bins.chuteHomedMsg');
			await loadLayout();
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.homingFailedFallback');
		} finally {
			homing = false;
		}
	}

	function isCurrentBin(bin: BinInfo): boolean {
		if (currentAngle === null) return false;
		return Math.abs(bin.angle - currentAngle) < 2;
	}

	function assignedSetMeta(categoryIds: string[]): SetMeta | null {
		if (!categoryIds || categoryIds.length !== 1) return null;
		const categoryId = categoryIds[0];
		const match = sortingProfileStore.data?.rules.find((rule) => {
			const candidate = rule as any;
			return candidate.id === categoryId && candidate.rule_type === 'set';
		}) as any;
		if (!match) return null;
		return {
			name: match.name,
			set_num: match.set_num,
			img_url: match.set_meta?.img_url
		};
	}

	function openBinDetails(layerIndex: number, bin: BinInfo) {
		detailsBin = { layerIndex, bin, contents: contentsForBin(layerIndex, bin) };
		detailsOpen = true;
	}

	const CONFIRM_KEYS: Record<string, { title: string; action: string }> = {
		'contents/clear:all': {
			title: 'routes.bins.confirmEmptyAllBinsTitle',
			action: 'routes.bins.confirmEmptyAllBinsAction'
		},
		'contents/clear:layer': {
			title: 'routes.bins.confirmEmptyLayerTitle',
			action: 'routes.bins.confirmEmptyLayerAction'
		},
		'contents/clear:bin': {
			title: 'routes.bins.confirmEmptyBinTitle',
			action: 'routes.bins.confirmEmptyBinAction'
		},
		'categories/clear:all': {
			title: 'routes.bins.confirmResetAllBinsTitle',
			action: 'routes.bins.confirmResetAllBinsAction'
		},
		'categories/clear:layer': {
			title: 'routes.bins.confirmResetLayerTitle',
			action: 'routes.bins.confirmResetLayerAction'
		},
		'categories/clear:bin': {
			title: 'routes.bins.confirmResetBinTitle',
			action: 'routes.bins.confirmResetBinAction'
		}
	};

	function hasAnyClearing(): boolean {
		return clearingStates.length > 0;
	}

	function hasClearingKey(busyKey: string): boolean {
		return clearingStates.some((state) => state.busyKey === busyKey);
	}

	function activeGlobalClearing(): ClearingState | null {
		return clearingStates.find((state) => state.scope === 'all') ?? null;
	}

	function isGlobalClearing(): boolean {
		return activeGlobalClearing() !== null;
	}

	function isLayerClearing(layerIndex: number): boolean {
		return clearingStates.some(
			(state) => state.scope === 'layer' && state.layerIndex === layerIndex
		);
	}

	function isBinClearing(layerIndex: number, sectionIndex: number, binIndex: number): boolean {
		return clearingStates.some(
			(state) =>
				state.scope === 'bin' &&
				state.layerIndex === layerIndex &&
				state.sectionIndex === sectionIndex &&
				state.binIndex === binIndex
		);
	}

	function globalClearingTitle(): string {
		const state = activeGlobalClearing();
		if (!state) return '';
		return state.endpoint === 'contents/clear'
			? $_('routes.bins.clearingAllBinsEmptyingTitle')
			: $_('routes.bins.clearingAllBinsResettingTitle');
	}

	function globalClearingDescription(): string {
		const state = activeGlobalClearing();
		if (!state) return '';
		return state.endpoint === 'contents/clear'
			? $_('routes.bins.clearingAllBinsKeepDesc')
			: $_('routes.bins.clearingAllBinsRemoveDesc');
	}

	function layerClearingLabel(layerIndex: number): string {
		const state =
			clearingStates.find(
				(entry) => entry.scope === 'layer' && entry.layerIndex === layerIndex
			) ?? null;
		if (!state) return '';
		return state.endpoint === 'contents/clear'
			? $_('routes.bins.clearingLayerEmptyingLabel', { values: { n: layerIndex + 1 } })
			: $_('routes.bins.clearingLayerResettingLabel', { values: { n: layerIndex + 1 } });
	}

	function binClearingLabel(layerIndex: number, sectionIndex: number, binIndex: number): string {
		const state =
			clearingStates.find(
				(entry) =>
					entry.scope === 'bin' &&
					entry.layerIndex === layerIndex &&
					entry.sectionIndex === sectionIndex &&
					entry.binIndex === binIndex
			) ?? null;
		if (!state) return '';
		return state.endpoint === 'contents/clear'
			? $_('routes.bins.emptyingBinLabel')
			: $_('routes.bins.resettingBinLabel');
	}

	async function runBinAction(
		endpoint: 'contents/clear' | 'categories/clear',
		scope: 'all' | 'layer' | 'bin',
		payload: { layer_index?: number; section_index?: number; bin_index?: number },
		confirmMessage: string,
		busyKey: string
	) {
		if (movingTo || homing || togglingLayerKey !== null || hasClearingKey(busyKey)) return;
		if (scope === 'all' ? hasAnyClearing() : isGlobalClearing()) return;
		const keys = CONFIRM_KEYS[`${endpoint}:${scope}`];
		if (
			!(await confirmDialog({
				title: $_(keys.title),
				message: confirmMessage,
				action: $_(keys.action),
				danger: endpoint === 'categories/clear'
			}))
		)
			return;

		const nextState: ClearingState = {
			endpoint,
			scope,
			busyKey,
			layerIndex: payload.layer_index,
			sectionIndex: payload.section_index,
			binIndex: payload.bin_index
		};
		clearingStates = [...clearingStates, nextState];
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/${endpoint}`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ scope, ...payload })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			statusMsg = data?.message ?? $_('routes.bins.binsUpdatedFallback');
			await loadLayout();
			await loadBinContents();
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.updateBinsFailedFallback');
		} finally {
			clearingStates = clearingStates.filter((state) => state.busyKey !== busyKey);
		}
	}

	// Dedicated full-reset path (layer or whole machine). The backend clears
	// assignments + contents in one call and returns the fresh layout + contents,
	// so we update state directly instead of firing extra GET round-trips.
	async function runBinReset(
		scope: 'all' | 'layer',
		layerIndex: number | undefined,
		confirmMessage: string,
		busyKey: string
	) {
		if (movingTo || homing || togglingLayerKey !== null || hasClearingKey(busyKey)) return;
		if (scope === 'all' ? hasAnyClearing() : isGlobalClearing()) return;
		const keys = CONFIRM_KEYS[`categories/clear:${scope}`];
		if (
			!(await confirmDialog({
				title: $_(keys.title),
				message: confirmMessage,
				action: $_(keys.action),
				danger: true
			}))
		)
			return;

		clearingStates = [
			...clearingStates,
			{ endpoint: 'categories/clear', scope, busyKey, layerIndex }
		];
		statusMsg = '';
		error = null;
		try {
			const url =
				scope === 'all'
					? `${baseUrl()}/api/bins/reset/machine`
					: `${baseUrl()}/api/bins/reset/layer/${layerIndex}`;
			const res = await fetch(url, { method: 'POST' });
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			statusMsg = data?.message ?? $_('routes.bins.binsResetFallback');
			if (data.layout) {
				layers = data.layout.layers ?? layers;
				currentAngle = data.layout.current_angle ?? null;
				activeLayer = data.layout.active_layer ?? null;
			}
			applyBinsContents(data.bins);
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.resetBinsFailedFallback');
		} finally {
			clearingStates = clearingStates.filter((state) => state.busyKey !== busyKey);
		}
	}

	async function toggleLayerEnabled(layerIndex: number, enabled: boolean) {
		if (movingTo || homing || hasAnyClearing() || togglingLayerKey !== null) return;
		togglingLayerKey = layerIndex;
		statusMsg = '';
		error = null;
		try {
			const payloadLayers = layers.map((layer) => ({
				bin_count: layer.bin_count,
				enabled: layer.layer_index === layerIndex ? enabled : layer.enabled,
				max_pieces_per_bin: layer.max_pieces_per_bin
			}));
			const res = await fetch(`${baseUrl()}/api/hardware-config/storage-layers`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ layers: payloadLayers })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			statusMsg = data?.message ?? $_('routes.bins.layerUpdatedFallback', { values: { n: layerIndex + 1 } });
			await loadLayout();
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.updateLayerStatusFailedFallback');
		} finally {
			togglingLayerKey = null;
		}
	}

	function sectionEnabled(layer: LayerInfo, sectionIndex: number): boolean {
		return layer.section_enabled?.[sectionIndex] ?? true;
	}

	// A column (same section index across every layer) is "on" only when every
	// layer has that section enabled.
	function columnEnabled(sectionIndex: number): boolean {
		return layers.every((layer) => sectionEnabled(layer, sectionIndex));
	}

	function maxSectionCount(): number {
		return layers.reduce((max, layer) => Math.max(max, layer.section_count ?? 0), 0);
	}

	async function setSectionEnabled(
		scope: 'section' | 'column' | 'all',
		enabled: boolean,
		opts: { layer_index?: number; section_index?: number },
		busyKey: string
	) {
		if (movingTo || homing || hasAnyClearing() || sectionBusyKey !== null) return;
		sectionBusyKey = busyKey;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/sections/enabled`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ scope, enabled, ...opts })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			if (data.layout?.layers) {
				layers = data.layout.layers;
				currentAngle = data.layout.current_angle ?? currentAngle;
				activeLayer = data.layout.active_layer ?? activeLayer;
			}
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.updateSectionStatusFailedFallback');
		} finally {
			sectionBusyKey = null;
		}
	}

	async function pointAtSection(sectionIndex: number) {
		const busyKey = `point-${sectionIndex}`;
		if (movingTo || homing || pointingSectionKey !== null) return;
		pointingSectionKey = busyKey;
		statusMsg = '';
		error = null;
		try {
			const res = await fetch(`${baseUrl()}/api/bins/move-to-section`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ section_index: sectionIndex })
			});
			if (!res.ok) {
				const detail = await res.json().catch(() => null);
				throw new Error(detail?.detail ?? `HTTP ${res.status}`);
			}
			const data = await res.json();
			statusMsg = $_('routes.bins.pointingAtSectionMsg', {
				values: { n: sectionIndex + 1, angle: data.target_angle }
			});
		} catch (e: unknown) {
			error = e instanceof Error ? e.message : $_('routes.bins.pointAtSectionFailedFallback');
		} finally {
			pointingSectionKey = null;
		}
	}

	onMount(() => {
		void loadLayout();
		void loadBinContents();
		void loadSetProgress();
		void loadBinSettings();
		void sortingProfileStore.load(baseUrl()).catch(() => {});
		// Auto-update without hammering the machine: the fast tick fetches the
		// small layout payload (live chute angle) plus a tiny contents version
		// token — the heavy contents fetch only runs when that token changes.
		// Set progress moves to a slow multiple, and everything pauses while the
		// tab is hidden so a backgrounded tab can't saturate the machine's uplink.
		let tick = 0;
		const interval = setInterval(() => {
			if (document.hidden) return;
			tick += 1;
			void loadLayout();
			void pollBinContentsVersion();
			if (tick % 5 === 0) void loadSetProgress();
		}, 2000);
		const onVisibilityChange = () => {
			if (document.hidden) return;
			void loadLayout();
			void loadBinContents();
			void loadSetProgress();
		};
		document.addEventListener('visibilitychange', onVisibilityChange);
		return () => {
			clearInterval(interval);
			document.removeEventListener('visibilitychange', onVisibilityChange);
		};
	});

	$effect(() => {
		const nextBaseUrl = baseUrl();
		if (nextBaseUrl === activeBaseUrl) return;
		activeBaseUrl = nextBaseUrl;
		loading = true;
		error = null;
		statusMsg = '';
		layers = [];
		contentsByKey = {};
		contentsLoaded = false;
		contentsVersion = null;
		setProgressByCategoryId = {};
		detailsOpen = false;
		detailsBin = null;
		movingTo = null;
		homing = false;
		clearingStates = [];
		togglingLayerKey = null;
		searchQuery = '';
		void loadLayout();
		void loadBinContents();
		void loadSetProgress();
		void loadBinSettings();
		void sortingProfileStore.load(nextBaseUrl).catch(() => {});
	});

	// Keep the details modal pointed at fresh bin/contents objects as the live
	// polling replaces layers and contentsByKey.
	$effect(() => {
		const currentDetails = detailsBin;
		if (!currentDetails) return;
		const layer = layers.find((entry) => entry.layer_index === currentDetails.layerIndex);
		const bin = layer?.bins.find(
			(entry) =>
				entry.section_index === currentDetails.bin.section_index &&
				entry.bin_index === currentDetails.bin.bin_index
		);
		if (!bin) return;
		const contents = contentsForBin(currentDetails.layerIndex, bin);
		if (currentDetails.bin === bin && currentDetails.contents === contents) return;
		detailsBin = {
			layerIndex: currentDetails.layerIndex,
			bin,
			contents
		};
	});
</script>

<svelte:head><title>{$_('routes.bins.pageTitle')}</title></svelte:head>

<AppShell>
	<div class="mx-auto flex w-full max-w-[1500px] flex-col gap-(--gap-panels) px-4 py-6 sm:px-6">
		<PageHeader title={$_('routes.bins.title')} description={$_('routes.bins.description')}>
			{#snippet actions()}
				<BinsHeaderActions
					csvUrl={currentContentsCsvUrl()}
					{homing}
					emptyBusy={hasClearingKey('empty-all')}
					resetBusy={hasClearingKey('reset-all')}
					disabled={homing || !!movingTo || hasAnyClearing() || togglingLayerKey !== null}
					onSnapshots={() => (snapshotsOpen = true)}
					onHome={() => void homeChute()}
					onEmptyAll={() =>
						void runBinAction(
							'contents/clear',
							'all',
							{},
							$_('routes.bins.emptyAllConfirmMessage'),
							'empty-all'
						)}
					onResetAll={() =>
						void runBinReset(
							'all',
							undefined,
							$_('routes.bins.resetAllConfirmMessage'),
							'reset-all'
						)}
				/>
			{/snippet}
		</PageHeader>

		<BinLayoutSection baseUrl={baseUrl()} profileId={activeProfileId} profileName={activeProfileName} />

		<Panel flush>
			<div class="divide-y divide-line">
				<SettingRow
					label={$_('routes.bins.allowMultiCategoryLabel')}
					help={$_('routes.bins.allowMultiCategoryHelp')}
				>
					<Switch
						checked={allowMultiCategory}
						label={$_('routes.bins.allowMultiCategoryLabel')}
						disabled={savingMultiCategory}
						onchange={() => void toggleMultiCategory()}
					/>
				</SettingRow>
				<SettingRow
					label={$_('routes.bins.autoAssignLabel')}
					help={$_('routes.bins.autoAssignHelp')}
					below={autoAssignResult ? autoAssignNote : undefined}
				>
					<Button loading={autoAssignBusy} onclick={() => void runAutoAssign(false)}>
						{autoAssignBusy ? $_('routes.bins.assigningLabel') : $_('routes.bins.autoAssignButton')}
					</Button>
					<Button loading={autoAssignBusy} onclick={() => void runAutoAssign(true)}>
						{autoAssignBusy ? $_('routes.bins.assigningLabel') : $_('routes.bins.autoAssignOverlapButton')}
					</Button>
				</SettingRow>
			</div>
		</Panel>

		{#if !loading && layers.length > 0 && maxSectionCount() > 0}
			<ColumnsPanel
				sectionCount={maxSectionCount()}
				{columnEnabled}
				{sectionBusyKey}
				pointingKey={pointingSectionKey}
				toggleDisabled={homing || !!movingTo || hasAnyClearing() || sectionBusyKey !== null}
				pointDisabled={homing || !!movingTo || pointingSectionKey !== null}
				onToggleColumn={(sectionIndex, enabled) =>
					void setSectionEnabled('column', enabled, { section_index: sectionIndex }, `col-${sectionIndex}`)}
				onPointSection={(sectionIndex) => void pointAtSection(sectionIndex)}
			/>
		{/if}

		{#if statusMsg}<Alert tone="success">{statusMsg}</Alert>{/if}
		{#if error}<Alert tone="danger">{error}</Alert>{/if}

		{#if !loading && layers.length > 0}
			<BinSearchBar bind:query={searchQuery} matchCount={searchMatchCount} totalBins={totalBinCount} />
		{/if}

		{#if loading}
			<div class="flex flex-col gap-(--gap-panels)" aria-busy="true">
				{#each Array(2) as _layerUnused}
					<Panel>
						<div class="mb-4 flex items-center justify-between gap-4">
							<Skeleton class="h-5 w-40" />
							<Skeleton class="h-6 w-16" />
						</div>
						<div class="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-6">
							{#each Array(6) as _binUnused}
								<div class="flex flex-col gap-2 rounded-control bg-well p-2.5">
									<Skeleton class="h-5 w-3/4" />
									<div class="grid grid-cols-4 gap-2">
										{#each Array(4) as _thumbUnused}
											<Skeleton class="aspect-square w-full" />
										{/each}
									</div>
									<Skeleton class="h-4 w-16" />
								</div>
							{/each}
						</div>
					</Panel>
				{/each}
			</div>
		{:else if layers.length === 0}
			<EmptyState title={$_('routes.bins.noStorageLayersTitle')} />
		{:else}
			{#each layers as layer (layer.layer_index)}
				{@const layerBusy = isLayerClearing(layer.layer_index)}
				{@const layerIsActive = activeLayer === layer.layer_index}
				<LayerPanel
					{layer}
					isActive={layerIsActive}
					{layerBusy}
					layerClearingLabel={layerClearingLabel(layer.layer_index)}
					emptyBusy={hasClearingKey(`empty-layer-${layer.layer_index}`)}
					resetBusy={hasClearingKey(`reset-layer-${layer.layer_index}`)}
					niiBusy={niiBusyLayer === layer.layer_index}
					niiDisabled={niiBusyLayer !== null}
					controlsDisabled={homing || !!movingTo || hasAnyClearing() || togglingLayerKey !== null}
					clearDisabled={homing || !!movingTo || isGlobalClearing() || layerBusy || togglingLayerKey !== null}
					sectionToggleDisabled={homing || !!movingTo || hasAnyClearing() || sectionBusyKey !== null}
					pointDisabled={homing || !!movingTo || pointingSectionKey !== null}
					pointingKey={pointingSectionKey}
					{contentsLoaded}
					contentsFor={(bin) => contentsForBin(layer.layer_index, bin)}
					setMetaFor={(bin) => assignedSetMeta(bin.category_ids)}
					setProgressFor={(bin) => binSetProgress(bin.category_ids)}
					isCurrentBin={(bin) => isCurrentBin(bin) && layerIsActive}
					isMovingBin={(bin) => movingTo === `${layer.layer_index}-${bin.section_index}-${bin.bin_index}`}
					isClearingBin={(bin) => isBinClearing(layer.layer_index, bin.section_index, bin.bin_index)}
					binClearingLabel={(bin) => binClearingLabel(layer.layer_index, bin.section_index, bin.bin_index)}
					sectionEnabled={(sectionIndex) => sectionEnabled(layer, sectionIndex)}
					moveDisabled={!!movingTo || homing || hasAnyClearing()}
					{searchActive}
					searchMatch={(bin) => binMatchesSearch(layer.layer_index, bin)}
					onToggleEnabled={(enabled) => void toggleLayerEnabled(layer.layer_index, enabled)}
					onEmptyLayer={() =>
						void runBinAction(
							'contents/clear',
							'layer',
							{ layer_index: layer.layer_index },
							$_('routes.bins.emptyLayerConfirmMessage', { values: { n: layer.layer_index + 1 } }),
							`empty-layer-${layer.layer_index}`
						)}
					onResetLayer={() =>
						void runBinReset(
							'layer',
							layer.layer_index,
							$_('routes.bins.resetLayerConfirmMessage', { values: { n: layer.layer_index + 1 } }),
							`reset-layer-${layer.layer_index}`
						)}
					onToggleNii={(enabled) => void setLayerNotInInventory(layer.layer_index, enabled)}
					onToggleBinNii={(bin, enabled) => void setBinNotInInventory(layer.layer_index, bin, enabled)}
					onToggleSection={(sectionIndex, enabled) =>
						void setSectionEnabled(
							'section',
							enabled,
							{ layer_index: layer.layer_index, section_index: sectionIndex },
							`sec-${layer.layer_index}-${sectionIndex}`
						)}
					onPointSection={(sectionIndex) => void pointAtSection(sectionIndex)}
					onOpenDetails={(bin) => openBinDetails(layer.layer_index, bin)}
					onMoveTo={(bin) => void moveToBin(layer.layer_index, bin.section_index, bin.bin_index)}
					onEmptyBin={(bin) =>
						void runBinAction(
							'contents/clear',
							'bin',
							{ layer_index: layer.layer_index, section_index: bin.section_index, bin_index: bin.bin_index },
							$_('routes.bins.emptyBinConfirmMessage', { values: { n: bin.global_index + 1 } }),
							`empty-bin-${layer.layer_index}-${bin.section_index}-${bin.bin_index}`
						)}
					onResetBin={(bin) =>
						void runBinAction(
							'categories/clear',
							'bin',
							{ layer_index: layer.layer_index, section_index: bin.section_index, bin_index: bin.bin_index },
							$_('routes.bins.resetBinConfirmMessage', { values: { n: bin.global_index + 1 } }),
							`reset-bin-${layer.layer_index}-${bin.section_index}-${bin.bin_index}`
						)}
				/>
			{/each}

			<DiscardBinCard
				{layers}
				{exitBin}
				saving={savingExitBin}
				onSetExitBin={(layerIndex, sectionIndex, binIndex) => void setExitBin(layerIndex, sectionIndex, binIndex)}
				onClearExitBin={() => void clearExitBin()}
			/>
		{/if}
	</div>
</AppShell>

{#snippet autoAssignNote()}
	<p class="px-3 py-2 text-sm text-ink-muted">{autoAssignResult}</p>
{/snippet}

<Modal
	open={isGlobalClearing()}
	title={globalClearingTitle()}
	size="sm"
	dismissible={false}
	status={$_('routes.bins.refreshingBinStateStatus')}
>
	<p>{globalClearingDescription()}</p>
</Modal>

<SnapshotsModal bind:open={snapshotsOpen} baseUrl={baseUrl()} />

<BinDetailsModal
	bind:open={detailsOpen}
	{detailsBin}
	baseUrl={baseUrl()}
	{layers}
	onSaved={(message) => {
		statusMsg = message;
		void loadLayout();
	}}
	onError={(message) => (error = message)}
/>
