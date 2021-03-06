from __future__ import division
import pandas as pd
import numpy as np
import datetime
from RouteNode import RouteNode
from anytree import RenderTree
import copy

pixel_per_km = 5
avg_speed_per_min = 1
vehicle_capacity = 3

# Read in data as Pandas DataFrame
data = pd.read_csv('Simpsons.txt', sep='\t', skiprows=1, header=None)
data.columns = ['Requester', 'Trip #', 'Depart After', 'Arrive Before', 'X1', 'Y1', 'X2', 'Y2']

# Convert Departure/Arrival times to minutes after earliest stop (8:00am)
data[['Depart After', 'Arrive Before']] = data[['Depart After', 'Arrive Before']].apply(pd.to_datetime)
data.sort_values(['Depart After', 'Arrive Before'], inplace=True)
time_zero = data['Depart After'].iloc[0]
data['Depart After'] = data['Depart After'].apply(lambda x: (x-time_zero).total_seconds()/60)
data['Arrive Before'] = data['Arrive Before'].apply(lambda x: (x-time_zero).total_seconds()/60)

data['Origin Loc.'] = list(np.array(zip(data['X1'], data['Y1'])))
data['Dest. Loc.'] = list(np.array(zip(data['X2'], data['Y2'])))
data.drop(['X1', 'Y1', 'X2', 'Y2'], inplace=True, axis=1)


def recursive_routes(node, trip_data, trip_id_time_index, time_matrix, leaves, connect_to=None):
	'''Recursively create tree of possible routes while ensuring vehicle isn't full.'''
	for child_trip_id, child_pickup in node.route_options.iteritems():
		# Get name of requester
		requester = trip_data.loc[trip_data['Trip #']==child_trip_id, 'Requester'].item()
		
		# Create child route node based on whether they need to be picked up or dropped off
		if (child_pickup is True) and (node.num_in_car < vehicle_capacity):
			# Update future route options
			child_route_options = copy.deepcopy(node.route_options)
			child_route_options[child_trip_id] = False
			
			# Calculate trip time (determine locations and look up in time_matrix)
			child_loc_id = trip_id_time_index[child_trip_id]
			if node.trip_id is not None:
				if node.pickup is True:
					parent_loc_id = trip_id_time_index[node.trip_id]
				elif node.pickup is False:
					parent_loc_id = trip_id_time_index[node.trip_id]+len(trip_data)
			else:
				parent_loc_id = child_loc_id
			trip_time = time_matrix[parent_loc_id, child_loc_id]
			
			# Updating trip time by connecting to end of route in previous block (if applicable)
			if connect_to is not None:
				loc1 = data.loc[data['Trip #']==connect_to.trip_id, 'Dest. Loc.'].item()
				loc2 = data.loc[data['Trip #']==child_trip_id, 'Origin Loc.'].item()
				trip_time = np.linalg.norm(loc1-loc2)/pixel_per_km/avg_speed_per_min
			
			# Checking if it's too early to be picked up; if so, must wait
			depart_after_time = trip_data.loc[trip_data['Trip #']==child_trip_id, 'Depart After'].item()
			if (node.total_time + trip_time) < depart_after_time:
				trip_time = depart_after_time - node.total_time
			
			child_node_name = 'Pickup %i:%s' % (child_trip_id, requester)
			child_loc = trip_data.loc[trip_data['Trip #']==child_trip_id, 'Origin Loc.'].item()
			child_route_node = RouteNode(child_node_name, child_trip_id, child_pickup, child_loc, child_route_options, trip_time, parent=node)
		elif child_pickup is False:
			# Update future route options
			child_route_options = copy.deepcopy(node.route_options)
			child_route_options[child_trip_id] = None
			
			# Calculate trip time (determine locations and look up in time_matrix)
			child_loc_id = trip_id_time_index[child_trip_id]+len(trip_data)
			if node.trip_id is not None:
				if node.pickup is True:
					parent_loc_id = trip_id_time_index[node.trip_id]
				elif node.pickup is False:
					parent_loc_id = trip_id_time_index[node.trip_id]+len(trip_data)
			else:
				parent_loc_id = child_loc_id
			trip_time = time_matrix[parent_loc_id, child_loc_id]
			
			child_node_name = 'Dropoff %i:%s' % (child_trip_id, requester)
			child_loc = trip_data.loc[trip_data['Trip #']==child_trip_id, 'Dest. Loc.'].item()
			child_route_node = RouteNode(child_node_name, child_trip_id, child_pickup, child_loc, child_route_options, trip_time, parent=node)
		else:
			pass # child_pickup == None implies trip has been completed, nothing to add
	
	# Recursively create routes
	for child in node.children:
		# If someone is late, prune branch (stop recursion)
		if not check_if_late(child, trip_data):
			recursive_routes(child, trip_data, trip_id_time_index, time_matrix, leaves)
		else:
			child.parent = None # prune infeasible child route
	
	# Keep track of end nodes in feasible routes (everyone who has been picked up has been dropped off on time)
	if len(node.children) == 0:
		if (False not in node.route_options.values()):
			leaves.append(node)


def check_if_late(node, trip_data):
	'''Given route_options state (i.e., who needs to be picked up/dropped off), check if anyone is late.'''
	for trip_id, pickup in node.route_options.iteritems():
		if trip_id == node.trip_id: # check if self is late
			arrive_before_time = trip_data.loc[trip_data['Trip #']==node.trip_id, 'Arrive Before'].item()
			if node.total_time > arrive_before_time: # late!
				return True
		else: # check if others (who have been picked up) are late
			arrive_before_time = trip_data.loc[trip_data['Trip #']==trip_id, 'Arrive Before'].item()
			if pickup is False and node.total_time > arrive_before_time: # late!:
				return True
	return False


def assign_vehicles(route_leaves, vehicles, previous_vehicles):
	'''Select best route in blocks, recursively find routes for trips left uncompleted and assign to new vehicle.'''
	# Sort routes by length and time taken to complete
	routes = pd.DataFrame(columns=['Last Node', 'Num. Stops', 'Total Time'])
	for l in route_leaves:
		routes = routes.append(pd.DataFrame([[l, len(l.ancestors), l.total_time]], columns=['Last Node', 'Num. Stops', 'Total Time']))
	sorted_routes = routes.sort_values(['Num. Stops', 'Total Time'], ascending=[False, True]).reset_index(drop=True)
	
	# Select route with shortest time and most trips completed as 'best'
	longest_route = sorted_routes.iloc[0]['Last Node']
	vehicles.append(longest_route)
	
	# Check for remaining trips, recurse and assign to new vehicle
	left_over = {}
	for trip_id, pickup in longest_route.route_options.iteritems():
		if pickup is True:
			left_over[trip_id] = pickup
	if len(left_over) != 0: # remaining trips exist
		if len(previous_vehicles) > len(vehicles): # previous block has connecting vehicle to use
			start = previous_vehicles[len(vehicles)]
		else: # brand new vehicle
			start = None
		new_block_leaves = []
		new_root_route = RouteNode(name='start', route_opt=left_over)
		recursive_routes(new_root_route, block_data, trip_id_time_index, time_matrix, new_block_leaves, start)
		assign_vehicles(new_block_leaves, vehicles, previous_vehicles)
	return vehicles


def itinerary(vehicles_full_trips):
	'''Pretty print itinerary for each vehicle.'''
	itinerary_str = ''
	for v, routes in enumerate(vehicles_full_trips):
		itinerary_str += '=========================================================\n'
		itinerary_str += 'VEHICLE %s\n' % str(v+1)
		itinerary_str += '=========================================================\n'
		for node in routes:
			itinerary_str += node.route_to_node(time_zero)
	return itinerary_str


if __name__ == '__main__':
	# Exploit information about there being only 5 customers and attempt to find "blocks" of rides that have to be completed before the next "block" starts
	blocks = [0] # array holding indices of block start/stops
	for i in xrange(len(data)-1):
		if (data.iloc[i]['Arrive Before'] < data.iloc[i+1]['Depart After']):
			blocks.append(i+1)
	blocks.append(len(data))
	
	vehicles_routes = [[] for b in xrange(len(blocks)-1)]
	
	# Divide and conquer: for each block find optimal route(s)
	for b in xrange(len(blocks)-1):
		# Calculate distances between all origin/destination locations within a block
		block_data = data.iloc[blocks[b]:blocks[b+1]]
		origin_loc = list(block_data['Origin Loc.'])
		dest_loc = list(block_data['Dest. Loc.'])
		all_loc = origin_loc + dest_loc
		dist_matrix = np.zeros((2*len(block_data),2*len(block_data)))
		for i, i_loc in enumerate(all_loc):
			for j, j_loc in enumerate(all_loc):
				dist_matrix[i,j] = np.linalg.norm(i_loc-j_loc)/pixel_per_km
		time_matrix = dist_matrix/avg_speed_per_min
		
		# Define possible route starts within a block
		route_options = {block_data.iloc[i]['Trip #']: True for i in xrange(len(block_data))}
		trip_id_time_index = {block_data.iloc[i]['Trip #']: i for i in xrange(len(block_data))}
		root_route = RouteNode(name='start', route_opt=route_options)
		
		block_leaves = []
		
		# Recursively build possible routes within a block
		if b == 0:
			start = None
			previous_vehicles = []
		else:
			start = vehicles_routes[b-1][0] # connect to vehicle's end of route in previous block
			previous_vehicles = vehicles_routes[b-1]
		
		recursive_routes(root_route, block_data, trip_id_time_index, time_matrix, block_leaves, start)
		
		vehicles = []
		vehicles_routes[b] = assign_vehicles(block_leaves, vehicles, previous_vehicles)
	
	# Given vehicle routes for each block of time, create itinerary for vehicles
	num_vehicles = 0
	for block in vehicles_routes:
		num_vehicles = max(len(block),num_vehicles)
	vehicles_full_trips = [[] for n in xrange(num_vehicles)]
	for block in vehicles_routes:
		for v in xrange(num_vehicles):
			if v < len(block):
				vehicles_full_trips[v].append(block[v])
	
	# Pretty print itinerary
	print itinerary(vehicles_full_trips)